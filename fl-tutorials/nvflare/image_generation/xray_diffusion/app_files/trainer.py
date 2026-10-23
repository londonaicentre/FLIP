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

"""FLARE Client API script for the FLIP pixel-space diffusion tutorial.

Trains a denoising diffusion model (MONAI's ``DiffusionModelUNet``) to generate 2-D chest X-rays, as
an ordinary FedAvg job. On a ``train`` task the client trains the received model for ``LOCAL_ROUNDS``
epochs and sends back the weight difference; on an ``evaluate`` task it scores the aggregated model
on its held-out split.

Training noises each image at a random timestep and teaches the UNet to predict that noise (MSE).
Every ``VALIDATE_EVERY`` epochs the same loss is scored on the held-out split and, in local runs,
sample images are drawn. Training runs in full precision.
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
from transforms import get_xray_transforms

# plot_utils.py is a local-development helper (../dev/): job.py stages it into simulator runs, and it is
# never uploaded, so on a trust this import fails and debug output is simply off.
try:
    from plot_utils import samples_enabled, save_grid
except ModuleNotFoundError as err:
    if err.name != "plot_utils":
        raise

    def samples_enabled(config: dict) -> bool:
        return False

    def save_grid(*args: object, **kwargs: object) -> None:
        return None


logger = logging.getLogger(__name__)

#: Epochs between in-loop validation and sampling when ``VALIDATE_EVERY`` is absent from the config.
DEFAULT_VALIDATE_EVERY = 10

#: Images drawn per sample grid.
NUM_DEBUG_SAMPLES = 4

#: Max gradient norm when ``GRAD_CLIP_NORM`` is absent from the config.
DEFAULT_GRAD_CLIP_NORM = 1.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project_id", type=str, default="")
    return parser.parse_args()


def load_query() -> str:
    """Read the cohort query from the client app config.

    FLARE's TaskScriptRunner does a naive whitespace split on task_script_args, so the SQL query
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


def validate_every(config: dict) -> int:
    """Epochs between in-loop validation and sampling, from ``VALIDATE_EVERY``; zero or less turns it off.

    The default is above 1 because the pass also samples, which runs the full reverse diffusion.
    """
    return int(config.get("VALIDATE_EVERY", DEFAULT_VALIDATE_EVERY))


def due_for_validation(epoch: int, epochs: int, config: dict) -> bool:
    """Whether this 0-based epoch validates: every ``VALIDATE_EVERY`` epochs, and always the last."""
    interval = validate_every(config)
    if interval <= 0:
        return False
    return (epoch + 1) % interval == 0 or epoch == epochs - 1


def grad_clip_norm(config: dict) -> float:
    """Max global gradient norm, from ``GRAD_CLIP_NORM``; zero or less turns clipping off."""
    return float(config.get("GRAD_CLIP_NORM", DEFAULT_GRAD_CLIP_NORM))


def lr_at(epoch: int, config: dict) -> float:
    """The learning rate at an epoch of the job: half a cosine from ``LR_START`` to ``LR_END``, then held.

    The decay spans the job's first ``LR_DECAY_EPOCHS`` epochs, so ``epoch`` counts across rounds
    (``global_round * LOCAL_ROUNDS + epoch``). Without ``LR_END`` or a positive ``LR_DECAY_EPOCHS`` the
    rate stays at ``LR_START``.
    """
    start = float(config["LR_START"])
    end = float(config.get("LR_END", start))
    decay_epochs = int(config.get("LR_DECAY_EPOCHS", 0) or 0)
    if decay_epochs <= 0:
        return start
    progress = min(epoch, decay_epochs) / decay_epochs
    return end + (start - end) * 0.5 * (1.0 + math.cos(math.pi * progress))


def set_learning_rate(optimizer: torch.optim.Optimizer, lr: float) -> None:
    for group in optimizer.param_groups:
        group["lr"] = lr


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


def split_for_site(items: list, site_name: str) -> list:
    """In the simulator, give "site-1" and "site-2" half the shared dataset each; on a trust, all of it."""
    half = len(items) // 2
    site = site_name.replace("-", "")
    if site == "site1":
        return items[:half]
    if site == "site2":
        return items[half:]
    return items


def noise_prediction_loss(
    model: torch.nn.Module, inferer: DiffusionInferer, scheduler: DDPMScheduler, images: torch.Tensor
) -> torch.Tensor:
    """The diffusion objective: noise each image at a random timestep and score the predicted noise."""
    noise = torch.randn_like(images)
    timesteps = torch.randint(0, scheduler.num_train_timesteps, (images.shape[0],), device=images.device)
    noise_pred = inferer(inputs=images, diffusion_model=model.diffusion_model, noise=noise, timesteps=timesteps)
    return torch.nn.functional.mse_loss(noise_pred.float(), noise.float())


def score_split(
    model: torch.nn.Module,
    loader: DataLoader,
    inferer: DiffusionInferer,
    scheduler: DDPMScheduler,
    device: torch.device,
) -> float:
    """Mean noise-prediction loss over a split, in eval mode; restores the caller's mode afterwards."""
    was_training = model.diffusion_model.training
    model.diffusion_model.eval()
    losses = []
    try:
        with torch.no_grad():
            for batch in loader:
                losses.append(noise_prediction_loss(model, inferer, scheduler, batch["image"].to(device)).item())
    finally:
        model.diffusion_model.train(was_training)
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
    """Generate ``NUM_DEBUG_SAMPLES`` images and save them as one grid, in local runs only.

    Samples are the honest read on a diffusion model: the loss barely moves between a model that
    generates radiographs and one that generates texture.
    """
    if not samples_enabled(config):
        return
    shape = [NUM_DEBUG_SAMPLES, config["net_config"]["diffusion_model"]["in_channels"], *config["spatial_shape"]]
    was_training = model.diffusion_model.training
    model.diffusion_model.eval()
    try:
        with torch.no_grad():
            samples = inferer.sample(
                input_noise=torch.randn(shape, device=device),
                diffusion_model=model.diffusion_model,
                scheduler=scheduler,
                save_intermediates=False,
                # No progress bar: simulated sites share one terminal and would overwrite each other's.
                verbose=False,
            )
    finally:
        model.diffusion_model.train(was_training)
    save_grid({"sample": samples}, "samples", config, site_name=flare.get_site_name(), step=step)


class DiffusionTrainer:
    """The model, optimiser, schedules and data for pixel-space diffusion training.

    One instance lives for the whole ``flare.is_running()`` loop, so the optimiser state carries over
    from round to round.
    """

    def __init__(self, config: dict, project_id: str, query: str):
        self.config = config
        self.project_id = project_id
        self.epochs = config["LOCAL_ROUNDS"]

        self.model = get_model()
        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        self.optimizer = torch.optim.Adam(
            self.model.diffusion_model.parameters(), lr=config["LR_START"], weight_decay=1e-6, amsgrad=True
        )
        self.noise_scheduler = DDPMScheduler(
            num_train_timesteps=1000,
            schedule="scaled_linear_beta",
            clip_sample=False,
            prediction_type="epsilon",
            beta_start=0.0015,
            beta_end=0.0195,
        )
        self.inferer = DiffusionInferer(scheduler=self.noise_scheduler)

        # Data
        self.flip = FLIP()
        site_name = flare.get_site_name()
        site_paths = site_data_paths(site_name)
        if site_paths is not None:
            # Local run on per-site data: this site's own folder and CSV, used whole.
            self.images_dir, dataframe_path = site_paths
            dataframe = pd.read_csv(dataframe_path, sep=None, engine="python")
            logger.info(f"Reading {dataframe_path} and {self.images_dir}")
        else:
            self.images_dir = None
            dataframe = self.flip.get_dataframe(project_id=project_id, query=query)
        train_items, val_items = self.build_datalist(dataframe)
        if site_paths is None:
            train_items = split_for_site(train_items, site_name)

        workers = int(config.get("DATALOADER_WORKERS", 4))  # as in Ark+ (FLIP#1434)
        self.train_loader = DataLoader(
            Dataset(train_items, transform=get_xray_transforms()),
            batch_size=config["BATCH_SIZE"],
            shuffle=True,
            num_workers=workers,
        )
        self.val_loader = DataLoader(
            Dataset(val_items, transform=get_xray_transforms(is_validation=True)),
            batch_size=config["BATCH_SIZE"],
            shuffle=False,
            num_workers=workers,
        )

    def build_datalist(self, dataframe: pd.DataFrame) -> tuple[list, list]:
        """List each accession's readable DICOM images and split them into train and validation.

        As ``xray_classification``'s ``build_datalist``, without the labels. Each header is parsed up
        front, so an unreadable file is dropped here rather than failing a training step.
        """
        datalist: list[dict[str, str]] = []
        for accession_id in dataframe["accession_id"]:
            if self.images_dir is not None:
                accession_folder_path = self.images_dir / str(accession_id)
            else:
                try:
                    accession_folder_path = self.flip.get_by_accession_number(
                        self.project_id, accession_id, resource_type=[ResourceType.DICOM]
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
        val_size = int(self.config["VAL_SPLIT"] * len(datalist))
        return datalist[val_size:], datalist[:val_size]

    def load_weights(self, weights: dict[str, torch.Tensor]) -> None:
        self.model.load_state_dict(state_dict=weights)
        self.model.diffusion_model.to(self.device)

    def train_one_epoch(self) -> float:
        """One pass over the training split; returns the mean loss."""
        self.model.diffusion_model.train()
        max_norm = grad_clip_norm(self.config)
        total = 0.0
        for batch in self.train_loader:
            self.optimizer.zero_grad(set_to_none=True)
            loss = noise_prediction_loss(self.model, self.inferer, self.noise_scheduler, batch["image"].to(self.device))
            if not torch.isfinite(loss):
                raise RuntimeError("Non-finite loss during training; stopping this site.")
            loss.backward()
            if max_norm > 0:
                torch.nn.utils.clip_grad_norm_(self.model.diffusion_model.parameters(), max_norm)
            self.optimizer.step()
            total += loss.item()
        return total / max(1, len(self.train_loader))

    def train(self, writer: SummaryWriter, global_round: int) -> int:
        """One ``train`` task: ``LOCAL_ROUNDS`` epochs. Returns the number of optimiser steps."""
        for epoch in range(self.epochs):
            step = global_round * self.epochs + epoch + 1
            lr = lr_at(step - 1, self.config)
            set_learning_rate(self.optimizer, lr)
            epoch_loss = self.train_one_epoch()
            logger.info(f"Epoch {epoch + 1} / {self.epochs}: loss {epoch_loss}")
            writer.add_scalar("Total loss DM@epoch", epoch_loss, global_step=step)
            writer.add_scalar("LR DM@epoch", lr, global_step=step)

            if due_for_validation(epoch, self.epochs, self.config):
                val_loss = score_split(self.model, self.val_loader, self.inferer, self.noise_scheduler, self.device)
                logger.info(f"Validation loss: {val_loss}")
                writer.add_scalar("Validation loss DM@epoch", val_loss, global_step=step)
                sample_and_save(self.model, self.inferer, self.noise_scheduler, self.config, self.device, step=step)

        return self.epochs * len(self.train_loader)

    def validate(self, writer: SummaryWriter) -> float:
        """One ``evaluate`` task: score the aggregated model on this site's held-out split, and sample."""
        val_loss = score_split(self.model, self.val_loader, self.inferer, self.noise_scheduler, self.device)
        sample_and_save(self.model, self.inferer, self.noise_scheduler, self.config, self.device)
        writer.add_scalar("Total loss DM (val)", val_loss, global_step=0)
        return val_loss


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

        global_round = input_model.current_round or 0

        if flare.is_train():
            logger.info(f"[diffusion trainer] received train task (round {global_round})")
            trainer.load_weights(to_torch_weights(input_model))
            n_iterations = trainer.train(writer, global_round)
            send_weight_diff(input_model.params, trainer.model, n_iterations)

        elif flare.is_evaluate():
            trainer.load_weights(to_torch_weights(input_model))
            val_loss = trainer.validate(writer)
            logger.info(f"Validating the aggregated diffusion model on {flare.get_site_name()}'s data: {val_loss}")
            flare.send(flare.FLModel(metrics={"val_loss": val_loss}))

        else:
            logger.warning("Received unknown task; ignoring.")


if __name__ == "__main__":
    main()
