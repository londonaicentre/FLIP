# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""NVFLARE Client API script for the FLIP pixel-space diffusion tutorial.

Trains a ``DiffusionModelUNet`` to denoise **images directly**, as an ordinary single-stage FedAvg
job: one ``train`` task and one ``validate`` task, dispatched on ``flare.is_train()`` /
``flare.is_evaluate()``.

The denoising objective is the same as the latent diffusion tutorial's — sample a timestep, add
noise, predict it, score with MSE — but it runs at full image resolution, so there is no
autoencoder in the loop and none of the latent-geometry machinery that comes with one:

* ``DiffusionInferer`` replaces ``LatentDiffusionInferer`` (it takes only a scheduler, and no
  ``autoencoder_model`` argument);
* there is no latent scale factor to derive, so no sample batch has to be encoded before training;
* the noise tensor is sampled at **image** shape, not at a padded latent shape.

The trade-off is cost: denoising at full image resolution is heavier than denoising a compressed
latent — the gap is far starker in 3-D, but it is the same trade-off here. See the README on sizing
``net_config`` and ``BATCH_SIZE``.

Everything else — the train loop, the ``VALIDATE_EVERY`` cadence for in-loop validation and sampling,
the shared :func:`score_split` / :func:`sample_and_save` helpers used by both the in-loop pass and the
round-boundary ``validate`` task, and what gets logged — mirrors the latent tutorial's trainer, so the
two can be compared like for like. Samples are written with ``save_grid`` rather than
``save_triplanar`` because the data is 2-D.
"""

import argparse
import json
import logging
import math
import os
from pathlib import Path

import numpy as np
import nvflare.client as flare
import pandas as pd
import pydicom
import torch
from flip import FLIP
from flip.constants import ResourceType
from models import get_model
from monai.data import DataLoader, Dataset
from monai.inferers import DiffusionInferer
from monai.networks.schedulers import DDPMScheduler
from nvflare.client.tracking import SummaryWriter
from plot_utils import samples_enabled, save_grid
from torch.amp import GradScaler, autocast
from torch.optim.lr_scheduler import LambdaLR
from transforms import get_xray_transforms

logger = logging.getLogger(__name__)

#: Epochs between in-loop validation and sampling when ``VALIDATE_EVERY`` is absent from the config.
DEFAULT_VALIDATE_EVERY = 10

#: Images drawn per sample grid. Fixed, like the latent tutorial's one volume per modality; kept within
#: ``plot_utils``' default tile cap so ``save_grid`` writes every image it is given.
NUM_DEBUG_SAMPLES = 4

#: Max gradient norm when ``GRAD_CLIP_NORM`` is absent from the config.
DEFAULT_GRAD_CLIP_NORM = 1.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project_id", type=str, default="")
    return parser.parse_args()


def load_query() -> str:
    """Read the cohort query from the client app config.

    NVFlare's TaskScriptRunner does a naive whitespace split on task_script_args, so the SQL query
    (which can contain spaces) is plumbed via the top-level ``query`` key in
    ``config/config_fed_client.json`` rather than as a CLI flag. In dev/simulator mode this is
    ignored by ``flip.get_dataframe``.
    """
    client_cfg = Path(__file__).parent.parent / "config" / "config_fed_client.json"
    if client_cfg.exists():
        try:
            return json.loads(client_cfg.read_text()).get("query", "")
        except Exception:
            return ""
    return ""


def load_config() -> dict:
    """Load the user-supplied config.json that sits next to this script."""
    config_path = Path(__file__).parent.resolve() / "config.json"
    with open(config_path) as f:
        return json.load(f)


def batch_accumulation_step(batch_size: int) -> int:
    """Accumulate gradients up to an effective batch of 8 when the configured batch is smaller."""
    if batch_size < 8:
        return 8 // batch_size
    return 1


def validate_every(config: dict) -> int:
    """Epochs between in-loop validation and sampling, from ``VALIDATE_EVERY`` (default 10).

    Zero or negative turns the in-loop pass off entirely, leaving only the round-boundary ``validate``
    task. Defaults above 1 for the same reason as the latent tutorial: the pass also samples, and a
    full reverse diffusion (every one of the scheduler's timesteps) is far costlier than a held-out
    forward pass.

    Args:
        config (dict): The parsed ``config.json``.

    Returns:
        int: The interval in epochs.
    """
    try:
        return int(config.get("VALIDATE_EVERY", DEFAULT_VALIDATE_EVERY))
    except (TypeError, ValueError):
        logger.warning(f"VALIDATE_EVERY is not an integer; falling back to {DEFAULT_VALIDATE_EVERY}.")
        return DEFAULT_VALIDATE_EVERY


def due_for_validation(epoch: int, epochs: int, config: dict) -> bool:
    """Whether this 0-based epoch gets an in-loop validation and sample.

    True every ``VALIDATE_EVERY`` epochs **and** always on the round's last epoch, so a round never
    ends without a fresh score.

    Args:
        epoch (int): 0-based epoch index within this round.
        epochs (int): Total epochs in this round.
        config (dict): The parsed ``config.json``.

    Returns:
        bool: True when this epoch should validate and sample.
    """
    interval = validate_every(config)
    if interval <= 0:
        return False
    return (epoch + 1) % interval == 0 or epoch == epochs - 1


def grad_clip_norm(config: dict) -> float:
    """Max global gradient norm for the diffusion UNet, from ``GRAD_CLIP_NORM`` (default 1.0).

    Zero or negative turns clipping off. Clipping is applied to the *unscaled* gradients, once per
    optimizer step (after accumulation), so the threshold means the same whatever ``BATCH_SIZE`` is.

    Args:
        config (dict): The parsed ``config.json``.

    Returns:
        float: The max norm; ``<= 0`` means no clipping.
    """
    try:
        return float(config.get("GRAD_CLIP_NORM", DEFAULT_GRAD_CLIP_NORM))
    except (TypeError, ValueError):
        logger.warning(f"GRAD_CLIP_NORM is not a number; falling back to {DEFAULT_GRAD_CLIP_NORM}.")
        return DEFAULT_GRAD_CLIP_NORM


def lr_at(epoch: int, config: dict) -> float:
    """The diffusion UNet's learning rate at a given epoch: cosine from ``LR_START`` to ``LR_END``.

    Over the first ``LR_DECAY_EPOCHS`` epochs the rate follows half a cosine from ``LR_START`` down to
    ``LR_END``, and holds at ``LR_END`` afterwards (``CosineAnnealingLR`` would climb back up past
    ``T_max``, which is why this drives a ``LambdaLR``). ``LR_END`` absent, or ``LR_DECAY_EPOCHS``
    absent or ``<= 0``, keeps the rate at ``LR_START``.

    The epoch counts across rounds: the trainer (and so the scheduler) lives for the whole
    ``flare.is_running()`` loop, so round 2 continues the decay where round 1 left it.

    Args:
        epoch (int): Epochs completed so far, across every round of this run.
        config (dict): The parsed ``config.json``.

    Returns:
        float: The learning rate for that epoch.
    """
    start = float(config["LR_START"])
    end = float(config.get("LR_END", start))
    decay_epochs = int(config.get("LR_DECAY_EPOCHS", 0) or 0)
    if decay_epochs <= 0:
        return start
    progress = min(epoch, decay_epochs) / decay_epochs
    return end + (start - end) * 0.5 * (1.0 + math.cos(math.pi * progress))


def site_data_paths(site_name: str) -> tuple[Path, Path] | None:
    """This simulated site's own ``(images_dir, dataframe)``, from ``SITE{N}_IMAGES_DIR`` / ``SITE{N}_DATAFRAME``.

    Set only by the tutorial Makefile for a local run on per-site data ("site-1" reads ``SITE1_*``).
    ``None`` when either is unset or blank: the site then reads the shared ``DEV_*`` dataset through
    ``FLIP``, as it does on a trust.
    """
    prefix = site_name.replace("-", "").upper()
    images_dir = os.environ.get(f"{prefix}_IMAGES_DIR", "").strip()
    dataframe = os.environ.get(f"{prefix}_DATAFRAME", "").strip()
    if not images_dir or not dataframe:
        return None
    return Path(images_dir), Path(dataframe)


def image_noise_shape(config: dict, batch_size: int) -> list[int]:
    """Noise shape for a pixel-space diffusion step: ``[B, image_channels, *spatial_shape]``.

    The latent tutorial pads the latent grid so the UNet can downsample it cleanly; here the UNet
    consumes the image directly, so the noise simply matches the image. ``spatial_shape`` must
    therefore be divisible by ``2 ** (len(channels) - 1)`` — the transform chain resizes every
    image to exactly that shape, so this is a config invariant rather than a per-batch check.
    """
    return [batch_size, config["net_config"]["diffusion_model"]["in_channels"], *config["spatial_shape"]]


def score_split(
    model: torch.nn.Module,
    loader: DataLoader,
    inferer: DiffusionInferer,
    scheduler: DDPMScheduler,
    config: dict,
    device: torch.device,
) -> float:
    """Mean noise-prediction MSE over a loader, with the diffusion model in eval mode.

    Shared by the in-loop pass and the round-boundary ``validate`` task so the two scores are computed
    identically. Restores the caller's train/eval mode afterwards, so the in-loop pass hands training
    back a model still in train mode.

    Args:
        model (torch.nn.Module): The diffusion network.
        loader (DataLoader): The split to score.
        inferer (DiffusionInferer): The inferer built for this round.
        scheduler (DDPMScheduler): The DDPM noise scheduler.
        config (dict): The parsed ``config.json``.
        device (torch.device): Device to run on.

    Returns:
        float: Mean MSE, or NaN when the loader is empty.
    """
    loss_fn = torch.nn.functional.mse_loss
    was_training = model.diffusion_model.training
    model.diffusion_model.eval()
    losses = []
    try:
        for batch in loader:
            images = batch["image"].to(device)
            with autocast(enabled=False, device_type=device.type):
                with torch.no_grad():
                    noise = torch.randn(image_noise_shape(config, images.shape[0])).to(device)
                    timesteps = torch.randint(
                        0, scheduler.num_train_timesteps, (images.shape[0],), device=device
                    ).long()
                    noise_pred = inferer(
                        inputs=images,
                        diffusion_model=model.diffusion_model,
                        noise=noise,
                        timesteps=timesteps,
                        condition=None,
                        mode="crossattn",
                    )
                losses.append(loss_fn(noise.float(), noise_pred.float()).item())
    finally:
        if was_training:
            model.diffusion_model.train()
    return float(np.mean(losses)) if losses else float("nan")


def sample_and_save(
    model: torch.nn.Module,
    inferer: DiffusionInferer,
    scheduler: DDPMScheduler,
    config: dict,
    device: torch.device,
    *,
    step: int | None = None,
) -> None:
    """Draw samples and write one grid. No-op unless samples are enabled.

    Sampling is the only honest read on a diffusion model: the noise-prediction MSE barely moves
    between a model that generates radiographs and one that generates texture. Gated on exactly what
    decides whether the grid is written, and nothing looser: this is a full reverse diffusion, so a
    broader test buys that cost and then discards the result inside ``save_grid``.

    Draws a fixed ``NUM_DEBUG_SAMPLES`` images per grid, as the latent tutorial draws a fixed one volume
    per modality: neither reads ``DEBUG_SAMPLES_MAX``, so the two configs carry the same debug keys.

    Args:
        model (torch.nn.Module): The diffusion network.
        inferer (DiffusionInferer): The inferer built for this round.
        scheduler (DDPMScheduler): The DDPM noise scheduler.
        config (dict): The parsed ``config.json``.
        device (torch.device): Device to run on.
        step (int | None): Epoch/round counter for the filename, so successive grids sort.
    """
    if not samples_enabled(config):
        return

    sample_count = NUM_DEBUG_SAMPLES
    logger.info(f"[DEBUG]: Sampling {sample_count} image(s)...")

    was_training = model.diffusion_model.training
    model.diffusion_model.eval()
    try:
        noise = torch.randn(image_noise_shape(config, sample_count)).to(device)
        with torch.no_grad():
            sampled_images = inferer.sample(
                input_noise=noise,
                diffusion_model=model.diffusion_model,
                scheduler=scheduler,
                save_intermediates=False,
                conditioning=None,
                # No tqdm bar: simulated sites share one terminal and their bars overwrite each other's line.
                verbose=False,
            )
    finally:
        if was_training:
            model.diffusion_model.train()

    logger.info(f"Sampled images shape: {tuple(sampled_images.shape)}")
    save_grid({"sample": sampled_images}, "samples", config, site_name=flare.get_site_name(), step=step)


class DiffusionTrainer:
    """Holds the model, loss, optimizer, scheduler, and data for pixel-space diffusion training.

    One instance lives for the whole ``flare.is_running()`` loop, so the optimizer state persists
    across global rounds.
    """

    def __init__(self, config: dict, project_id: str, query: str):
        self.config = config
        self.project_id = project_id

        self.params_diffusion = {"lr": config["LR_START"], "epochs": config.get("LOCAL_ROUNDS", 5)}

        # Model creation
        self.model = get_model()
        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

        self.losses_dm = {"loss": torch.nn.functional.mse_loss}
        self.optimizers_dm = {
            "optimizer": torch.optim.Adam(
                self.model.diffusion_model.parameters(), lr=self.params_diffusion["lr"], weight_decay=1e-6, amsgrad=True
            ),
            "scheduler": DDPMScheduler(
                num_train_timesteps=1000,
                schedule="scaled_linear_beta",
                clip_sample=False,
                prediction_type="epsilon",
                beta_start=0.0015,
                beta_end=0.0195,
            ),
        }
        # Cosine decay LR_START -> LR_END over LR_DECAY_EPOCHS epochs, stepped once per epoch (see lr_at).
        # LambdaLR takes a multiplier on the optimizer's initial rate, which is LR_START.
        self.lr_scheduler = LambdaLR(
            self.optimizers_dm["optimizer"], lambda epoch: lr_at(epoch, self.config) / self.params_diffusion["lr"]
        )

        # Data loading
        self.flip = FLIP()
        site_paths = site_data_paths(flare.get_site_name())
        if site_paths is not None:
            # Local run on per-site data: this site's own folder and CSV, used whole.
            self.images_dir, dataframe_path = site_paths
            dataframe = pd.read_csv(dataframe_path, sep=None, engine="python")
            logger.info(f"Reading {dataframe_path} and {self.images_dir}")
        else:
            self.images_dir = None
            dataframe = self.flip.get_dataframe(project_id=project_id, query=query)
        self.train_items, self.val_items = self.build_datalist(dataframe)

        # One shared DEV dataset in the simulator: halve it so the two sites train on different images.
        # Production trust names never match "site1"/"site2", so real runs are unsplit.
        site_name = flare.get_site_name().replace("-", "")
        if site_paths is None and site_name == "site1":
            self.train_items = self.train_items[: len(self.train_items) // 2]
        elif site_paths is None and site_name == "site2":
            self.train_items = self.train_items[len(self.train_items) // 2 :]

        self._train_dataset = Dataset(self.train_items, transform=get_xray_transforms())
        self._val_dataset = Dataset(self.val_items, transform=get_xray_transforms(is_validation=True))

    def build_datalist(self, dataframe) -> tuple[list, list]:
        """Fetch each accession's DICOM images and split into train/validation lists.

        Mirrors ``xray_classification``'s ``build_datalist`` with the label extraction removed: the
        cohort query still returns its lesion columns, but nothing here reads them. Each file's header
        is parsed before the path is accepted, so an unreadable DICOM is dropped now rather than
        failing a training step later.
        """
        datalist: list[dict[str, str]] = []

        for accession_id in dataframe["accession_id"]:
            if self.images_dir is not None:
                accession_folder_path = self.images_dir / str(accession_id)
            else:
                try:
                    accession_folder_path = self.flip.get_by_accession_number(
                        self.project_id,
                        accession_id,
                        resource_type=[
                            ResourceType.DICOM,
                        ],
                    )
                except Exception as err:
                    logger.info(f"Could not get image data folder path for {accession_id}: {err}")
                    continue

            for image in sorted(accession_folder_path.rglob("*.dcm")):
                try:
                    pydicom.dcmread(str(image), stop_before_pixels=True)
                except Exception as err:
                    logger.warning(f"Skipping invalid DICOM {image.name}: {err}")
                    continue
                datalist.append({"image": str(image)})

        logger.info(f"Found {len(datalist)} files in total.")

        # Validation / train splits:
        val_size = int(self.config["VAL_SPLIT"] * len(datalist))
        return datalist[val_size:], datalist[:val_size]

    def make_loaders(self, batch_size: int, shuffle: bool = True) -> tuple[DataLoader, DataLoader]:
        train_loader = DataLoader(self._train_dataset, batch_size=batch_size, shuffle=shuffle, num_workers=1)
        val_loader = DataLoader(self._val_dataset, batch_size=batch_size, shuffle=shuffle, num_workers=1)
        return train_loader, val_loader

    def val_loader(self, batch_size: int) -> DataLoader:
        return DataLoader(self._val_dataset, batch_size=batch_size, shuffle=False, num_workers=1)

    def load_weights(self, weights: dict[str, torch.Tensor]) -> None:
        self.model.load_state_dict(state_dict=weights, strict=False)

    def train(self, writer: SummaryWriter, global_round: int) -> int:
        """One ``train`` round: local diffusion-model epochs. Returns the iteration count."""
        accumulation_step = batch_accumulation_step(self.config["BATCH_SIZE"])
        train_loader, val_loader = self.make_loaders(self.config["BATCH_SIZE"])
        self.model.diffusion_model.to(device=self.device)

        inferer = DiffusionInferer(scheduler=self.optimizers_dm["scheduler"])
        scaler = GradScaler()

        # Basic training
        train_loss = []
        val_loss = []
        epochs = self.params_diffusion["epochs"]
        for epoch in range(epochs):
            self.model.diffusion_model.train()
            batch_acc_counter = 0
            train_loss_epoch = 0

            for batch in train_loader:
                images = batch["image"].to(self.device)

                with autocast(enabled=False, device_type=self.device.type):
                    noise = torch.randn(image_noise_shape(self.config, images.shape[0])).to(self.device)
                    timesteps = torch.randint(
                        0,
                        self.optimizers_dm["scheduler"].num_train_timesteps,
                        (images.shape[0],),
                        device=self.device,
                    ).long()
                    noise_pred = inferer(
                        inputs=images,
                        diffusion_model=self.model.diffusion_model,
                        noise=noise,
                        timesteps=timesteps,
                        condition=None,
                        mode="crossattn",
                    )
                    loss = self.losses_dm["loss"](noise.float(), noise_pred.float())

                if True in torch.isnan(loss) or True in torch.isnan(noise_pred):
                    logger.error("Found NaN on training loss; stopping training on site.")
                    raise RuntimeError("NaN loss during train")

                scaler.scale(loss).backward()
                batch_acc_counter += 1
                if batch_acc_counter == accumulation_step:
                    max_norm = grad_clip_norm(self.config)
                    if max_norm > 0:
                        # Unscale first so the norm is measured on the real gradients, not the
                        # GradScaler-inflated ones; scaler.step then knows not to unscale again.
                        scaler.unscale_(self.optimizers_dm["optimizer"])
                        torch.nn.utils.clip_grad_norm_(self.model.diffusion_model.parameters(), max_norm)
                    scaler.step(self.optimizers_dm["optimizer"])
                    scaler.update()
                    batch_acc_counter = 0
                    self.optimizers_dm["optimizer"].zero_grad(set_to_none=True)
                train_loss_epoch += loss.item()
                logger.debug(f"Batch loss {loss.item()}")

            train_loss.append(train_loss_epoch / max(1, len(train_loader)))

            # Report this epoch's loss, not np.mean(train_loss) — a running cumulative average that
            # flattens as the round goes on and lags real changes.
            epoch_loss = train_loss[-1]
            step = global_round * epochs + epoch + 1
            logger.info(f"Epoch {epoch + 1} / {epochs};\n Total loss DM: {epoch_loss}")
            writer.add_scalar("Total loss DM@epoch", epoch_loss, global_step=step)
            writer.add_scalar("LR DM@epoch", self.optimizers_dm["optimizer"].param_groups[0]["lr"], global_step=step)
            # Per epoch, after the epoch's last optimizer step. ``lr_scheduler`` is not in
            # ``optimizers_dm`` because that dict's "scheduler" is the DDPM noise scheduler.
            self.lr_scheduler.step()

            # Validation and sampling on a cadence, not every epoch (see validate_every).
            if due_for_validation(epoch, epochs, self.config):
                epoch_val_loss = score_split(
                    self.model,
                    val_loader,
                    inferer,
                    self.optimizers_dm["scheduler"],
                    self.config,
                    self.device,
                )
                val_loss.append(epoch_val_loss)
                logger.info(f"Validation DM: {epoch_val_loss}")
                writer.add_scalar("Validation loss DM@epoch", epoch_val_loss, global_step=step)
                sample_and_save(
                    self.model,
                    inferer,
                    self.optimizers_dm["scheduler"],
                    self.config,
                    self.device,
                    step=step,
                )

        return epochs * len(train_loader)


def validate(
    model: torch.nn.Module,
    test_loader: DataLoader,
    scheduler: DDPMScheduler,
    config: dict,
    device: torch.device,
    writer: SummaryWriter,
) -> float:
    """Score the aggregated diffusion model on the local held-out split.

    Args:
        model (torch.nn.Module): The diffusion network with the broadcast global weights loaded.
        test_loader (DataLoader): Held-out data loader.
        scheduler (DDPMScheduler): The DDPM noise scheduler.
        config (dict): The user app config (``config.json``).
        device (torch.device): Device to run on.
        writer (SummaryWriter): NVFLARE Client API metrics writer.

    Returns:
        float: Mean noise-prediction MSE over the held-out split.
    """
    model.diffusion_model.to(device=device)
    inferer = DiffusionInferer(scheduler=scheduler)
    model.diffusion_model.eval()

    # Same two helpers the in-loop pass uses, so the round-boundary score and the mid-round scores are
    # computed identically.
    mean_val_loss = score_split(model, test_loader, inferer, scheduler, config, device)
    sample_and_save(model, inferer, scheduler, config, device)

    logger.info(f"Validation DM: {mean_val_loss}")
    writer.add_scalar("Total loss DM (val)", mean_val_loss, global_step=0)

    return mean_val_loss


def to_torch_weights(input_model: flare.FLModel) -> dict[str, torch.Tensor]:
    return {k: torch.as_tensor(v) for k, v in input_model.params.items()}


def send_weight_diff(original_params: dict, model: torch.nn.Module, n_iterations: int) -> None:
    """Send the full-model weight diff for a completed training round.

    Built one tensor at a time to avoid holding a second full copy of the model in RAM (mirrors
    ``flip.utils.get_model_weights_diff``).
    """
    diff = {}
    for k, v in model.state_dict().items():
        new_arr = v.detach().cpu().numpy()
        diff[k] = new_arr - np.asarray(original_params[k])
        del new_arr

    flare.send(
        flare.FLModel(
            params=diff,
            params_type="DIFF",
            meta={"NUM_STEPS_CURRENT_ROUND": n_iterations},
        )
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    args = parse_args()
    config = load_config()

    flare.init()
    writer = SummaryWriter()

    trainer = DiffusionTrainer(config, project_id=args.project_id, query=load_query())

    while flare.is_running():
        input_model = flare.receive()
        if input_model is None:
            break

        weights = to_torch_weights(input_model)
        global_round = input_model.current_round or 0

        if flare.is_train():
            logger.info(f"[diffusion trainer] received train task (round {global_round})")
            trainer.load_weights(weights)
            n_iterations = trainer.train(writer, global_round)
            send_weight_diff(input_model.params, trainer.model, n_iterations)

        elif flare.is_evaluate():
            trainer.model.load_state_dict({k: v.to(trainer.device) for k, v in weights.items()})
            test_loader = trainer.val_loader(config["BATCH_SIZE"])
            val_loss = validate(
                trainer.model, test_loader, trainer.optimizers_dm["scheduler"], config, trainer.device, writer
            )
            logger.info(f"Validating the aggregated diffusion model on {flare.get_site_name()}'s data: {val_loss}")
            flare.send(flare.FLModel(metrics={"val_loss": val_loss}))

        else:
            logger.warning("Received unknown task; ignoring.")


if __name__ == "__main__":
    main()
