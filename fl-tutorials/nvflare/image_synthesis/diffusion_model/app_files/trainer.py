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
"""

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import nvflare.client as flare
import pydicom
import torch
from debug_samples import samples_enabled, save_grid
from flip import FLIP
from flip.constants import FlipConstants, ResourceType
from models import get_model
from monai.data import DataLoader, Dataset
from monai.inferers import DiffusionInferer
from monai.networks.schedulers import DDPMScheduler
from nvflare.client.tracking import SummaryWriter
from torch.amp import GradScaler, autocast
from transforms import get_xray_transforms

logger = logging.getLogger(__name__)


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


def image_noise_shape(config: dict, batch_size: int) -> list[int]:
    """Noise shape for a pixel-space diffusion step: ``[B, image_channels, *spatial_shape]``.

    The latent tutorial pads the latent grid so the UNet can downsample it cleanly; here the UNet
    consumes the image directly, so the noise simply matches the image. ``spatial_shape`` must
    therefore be divisible by ``2 ** (len(channels) - 1)`` — the transform chain resizes every
    image to exactly that shape, so this is a config invariant rather than a per-batch check.
    """
    return [batch_size, config["net_config"]["diffusion_model"]["in_channels"], *config["spatial_shape"]]


class DiffusionTrainer:
    """Holds the model, loss, optimizer, scheduler, and data for pixel-space diffusion training.

    One instance lives for the whole ``flare.is_running()`` loop, so the optimizer state persists
    across global rounds.
    """

    def __init__(self, config: dict, project_id: str, query: str):
        self.config = config
        self.project_id = project_id

        self.params_diffusion = {"lr": config["LR_DM"], "epochs": config.get("LOCAL_ROUNDS", 5)}

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

        # Data loading
        self.flip = FLIP()
        dataframe = self.flip.get_dataframe(project_id=project_id, query=query)
        self.train_items, self.val_items = self.build_datalist(dataframe)

        # Per-site split for local/simulator runs where every client reads the same DEV dataset;
        # in production each trust's data-access API already scopes the cohort to its own data.
        # The NVFLARE simulator names its clients "site-1"/"site-2"; normalise so the split actually applies
        # (as arkplus_fine_tuning's data_utils does). Production trust names never match, so real runs are unsplit.
        site_name = flare.get_site_name().replace("-", "")
        if site_name == "site1":
            self.train_items = self.train_items[: len(self.train_items) // 2]
        elif site_name == "site2":
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
                    scaler.step(self.optimizers_dm["optimizer"])
                    scaler.update()
                    batch_acc_counter = 0
                    self.optimizers_dm["optimizer"].zero_grad(set_to_none=True)
                train_loss_epoch += loss.item()

            train_loss.append(train_loss_epoch / max(1, len(train_loader)))

            # Validation loss
            val_loss_epoch = 0
            for batch in val_loader:
                images = batch["image"].to(self.device)
                self.model.diffusion_model.eval()
                with autocast(enabled=False, device_type=self.device.type):
                    noise = torch.randn(image_noise_shape(self.config, images.shape[0])).to(self.device)
                    timesteps = torch.randint(
                        0,
                        self.optimizers_dm["scheduler"].num_train_timesteps,
                        (images.shape[0],),
                        device=self.device,
                    ).long()
                    with torch.no_grad():
                        noise_pred = inferer(
                            inputs=images,
                            diffusion_model=self.model.diffusion_model,
                            noise=noise,
                            timesteps=timesteps,
                            condition=None,
                            mode="crossattn",
                        )
                    val_loss_epoch += self.losses_dm["loss"](noise.float(), noise_pred.float()).item()

            val_loss.append(val_loss_epoch / max(1, len(val_loader)))

            logger.info(f"Epoch {epoch + 1} / {epochs};\n Total loss DM: {np.mean(train_loss)}")

            step = global_round * epochs + epoch + 1
            writer.add_scalar("Total loss DM@epoch", float(np.mean(train_loss)), global_step=step)
            writer.add_scalar("Validation loss DM@epoch", float(np.mean(val_loss)), global_step=step)

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
    loss_fn = torch.nn.functional.mse_loss

    model.diffusion_model.to(device=device)
    inferer = DiffusionInferer(scheduler=scheduler)
    model.diffusion_model.eval()

    val_loss = []
    for batch in test_loader:
        images = batch["image"].to(device)
        with autocast(enabled=False, device_type=device.type):
            with torch.no_grad():
                noise = torch.randn(image_noise_shape(config, images.shape[0])).to(device)
                timesteps = torch.randint(0, scheduler.num_train_timesteps, (images.shape[0],), device=device).long()
                noise_pred = inferer(
                    inputs=images,
                    diffusion_model=model.diffusion_model,
                    noise=noise,
                    timesteps=timesteps,
                    condition=None,
                    mode="crossattn",
                )
            val_loss.append(loss_fn(noise.float(), noise_pred.float()).item())

    # Sampling is the only honest read on a diffusion model: the noise-prediction MSE above barely
    # moves between a model that generates radiographs and one that generates texture. Run it when
    # either the dev sanity check or client-local debug images are asked for — it is a full reverse
    # diffusion (num_train_timesteps steps), so it stays off the per-epoch path.
    if FlipConstants.LOCAL_DEV or samples_enabled(config):
        logger.info("[DEBUG]: Sampling images...")
        noise = torch.randn(image_noise_shape(config, config["BATCH_SIZE"])).to(device)
        sampled_images = inferer.sample(
            input_noise=noise,
            diffusion_model=model.diffusion_model,
            scheduler=scheduler,
            conditioning=None,
        )
        logger.info(f"Sampled images shape: {sampled_images.detach().cpu().numpy().shape}")
        save_grid({"sample": sampled_images}, "samples", config, site_name=flare.get_site_name())

    mean_val_loss = float(np.mean(val_loss)) if val_loss else float("nan")
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
