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

"""FLARE Client API script for the FLIP latent diffusion tutorial.

Trains a denoising diffusion model (MONAI's ``DiffusionModelUNet``) in the latent space of a frozen
autoencoder, on 3-D brain MRI, as an ordinary FedAvg job. On a ``train`` task the client trains the
received model for ``LOCAL_ROUNDS`` epochs and sends back the weight difference; on an ``evaluate``
task it scores the aggregated model on its held-out split.

The autoencoder is not trained here. The FL server loads it from the uploaded ``SERVER_CHECKPOINT``
into the round-0 global model, so it reaches every client in the broadcast; ``AGGREGATE_ONLY_REGEX``
keeps everything but ``diffusion_model.*`` out of aggregation. Each client checks that the
autoencoder it received is the one ``autoencoder_config.yaml`` describes, and keeps it frozen.

The UNet is told which MR sequence it is denoising (``MODALITIES``, one-hot through cross-attention;
see ``modality.py``), so one model can generate any of them. Training runs in full precision.
"""

import argparse
import json
import logging
import math
from pathlib import Path

import numpy as np
import nvflare.client as flare
import pandas as pd
import torch
from flip import FLIP
from flip.constants import ResourceType
from latent_utils import autoencoder_fingerprint, build_inferer
from modality import modality_of, one_hot_condition
from models import get_model
from monai.data import DataLoader, Dataset
from monai.inferers import LatentDiffusionInferer
from monai.networks.schedulers import DDPMScheduler
from nvflare.client.tracking import SummaryWriter
from transforms import get_brain_mri_transforms

# plot_utils.py is a local-development helper (../dev/): job.py stages it into simulator runs, and it is
# never uploaded, so on a trust this import fails and debug output is simply off.
try:
    from plot_utils import samples_enabled, save_triplanar
except ModuleNotFoundError as err:
    if err.name != "plot_utils":
        raise

    def samples_enabled(config: dict) -> bool:
        return False

    def save_triplanar(*args: object, **kwargs: object) -> None:
        return None


logger = logging.getLogger(__name__)

#: Epochs between in-loop validation and sampling when ``VALIDATE_EVERY`` is absent from the config.
DEFAULT_VALIDATE_EVERY = 10

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


def split_for_site(items: list[dict], site_name: str) -> list[dict]:
    """In the simulator, give "site-1" and "site-2" half the shared cohort each; on a trust, all of it.

    Split by accession, so the four sequences of one study stay on one site.
    """
    site = site_name.replace("-", "")
    if site not in ("site1", "site2"):
        return items
    accessions = list(dict.fromkeys(item["accession_id"] for item in items))
    half = len(accessions) // 2
    mine = set(accessions[:half] if site == "site1" else accessions[half:])
    return [item for item in items if item["accession_id"] in mine]


def batch_condition(batch: dict, config: dict, device: torch.device) -> torch.Tensor | None:
    """The batch's one-hot MR sequence for cross-attention, or None when the model is unconditional."""
    if not config["net_config"]["diffusion_model"]["with_conditioning"]:
        return None
    return one_hot_condition(batch["modality_index"], len(config["MODALITIES"]), device)


def freeze_autoencoder(model: torch.nn.Module) -> None:
    """Freeze the supplied autoencoder: no gradients, and always in eval mode."""
    model.autoencoder.requires_grad_(False)
    model.autoencoder.eval()


def verify_autoencoder(model: torch.nn.Module) -> None:
    """Refuse to go on unless the autoencoder that arrived is the one ``autoencoder_config.yaml`` describes.

    The server loads ``SERVER_CHECKPOINT`` with ``strict=False``: a checkpoint it cannot find, or whose
    keys match nothing, leaves a randomly initialised encoder in the broadcast, and training would
    report plausible losses against it. The YAML records the extracted weights' fingerprint.
    """
    received = autoencoder_fingerprint(model.autoencoder.state_dict())
    if received != model.autoencoder_sha256:
        raise RuntimeError(
            "The autoencoder this client received is not the one autoencoder_config.yaml describes "
            f"(weights fingerprint {received[:12]}…, expected {model.autoencoder_sha256[:12]}…). Look in the "
            "server log for 'SERVER_CHECKPOINT ... not found' or a 'Loaded backbone' line with unexpected keys, "
            "and prepare pretrained_autoencoder.pt and autoencoder_config.yaml together (make prepare-checkpoint)."
        )


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


def noise_prediction_loss(
    model: torch.nn.Module,
    inferer: LatentDiffusionInferer,
    scheduler: DDPMScheduler,
    images: torch.Tensor,
    condition: torch.Tensor | None,
    latent_shape: list[int],
) -> torch.Tensor:
    """The diffusion objective in latent space: encode, noise at a random timestep, score the predicted noise."""
    noise = torch.randn([images.shape[0], *latent_shape], device=images.device)
    timesteps = torch.randint(0, scheduler.num_train_timesteps, (images.shape[0],), device=images.device)
    noise_pred = inferer(
        inputs=images,
        diffusion_model=model.diffusion_model,
        autoencoder_model=model.autoencoder,
        noise=noise,
        timesteps=timesteps,
        condition=condition,
        mode="crossattn",
    )
    return torch.nn.functional.mse_loss(noise_pred.float(), noise.float())


def score_split(
    model: torch.nn.Module,
    loader: DataLoader,
    inferer: LatentDiffusionInferer,
    scheduler: DDPMScheduler,
    latent_shape: list[int],
    config: dict,
    device: torch.device,
) -> float:
    """Mean noise-prediction loss over a split, in eval mode; restores the caller's mode afterwards."""
    was_training = model.diffusion_model.training
    model.diffusion_model.eval()
    losses = []
    try:
        with torch.no_grad():
            for batch in loader:
                images = batch["image"].to(device)
                condition = batch_condition(batch, config, device)
                losses.append(noise_prediction_loss(model, inferer, scheduler, images, condition, latent_shape).item())
    finally:
        model.diffusion_model.train(was_training)
    return float(np.mean(losses)) if losses else float("nan")


def sample_and_save(
    model: torch.nn.Module,
    inferer: LatentDiffusionInferer,
    scheduler: DDPMScheduler,
    latent_shape: list[int],
    config: dict,
    device: torch.device,
    *,
    step: int | None = None,
) -> None:
    """Generate one volume per MR sequence and save them tri-planar, in local runs only.

    Samples are the honest read on a diffusion model: the loss barely moves between a model that
    generates brains and one that generates texture. They also exercise the frozen decoder, which
    nothing else in this job touches.
    """
    if not samples_enabled(config):
        return
    modalities = config["MODALITIES"]
    conditioned = config["net_config"]["diffusion_model"]["with_conditioning"]
    count = len(modalities) if conditioned else 1
    was_training = model.diffusion_model.training
    model.diffusion_model.eval()
    try:
        with torch.no_grad():
            samples = inferer.sample(
                input_noise=torch.randn([count, *latent_shape], device=device),
                conditioning=one_hot_condition(torch.arange(count), len(modalities), device) if conditioned else None,
                diffusion_model=model.diffusion_model,
                autoencoder_model=model.autoencoder,
                scheduler=scheduler,
                save_intermediates=False,
                # No progress bar: simulated sites share one terminal and would overwrite each other's.
                verbose=False,
            )
    finally:
        model.diffusion_model.train(was_training)
    # One labelled column per sequence; a generated volume has no ground truth to show beside it.
    volumes = (
        {modality: samples[index : index + 1] for index, modality in enumerate(modalities)}
        if conditioned
        else {"sample": samples[:1]}
    )
    save_triplanar(volumes, "samples", config, site_name=flare.get_site_name(), step=step)


class LatentDiffusionTrainer:
    """The model, optimiser, schedules and data for latent diffusion training.

    One instance lives for the whole ``flare.is_running()`` loop, so the optimiser state carries over
    from round to round.
    """

    def __init__(self, config: dict, project_id: str, query: str) -> None:
        self.config = config
        self.project_id = project_id
        self.epochs = config["LOCAL_ROUNDS"]

        self.model = get_model()
        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        freeze_autoencoder(self.model)
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
        self._inferer: tuple[LatentDiffusionInferer, list[int]] | None = None

        # Data
        self.flip = FLIP()
        train_items, val_items = self.build_datalist(self.flip.get_dataframe(project_id=project_id, query=query))
        train_items = split_for_site(train_items, flare.get_site_name())
        workers = int(config.get("DATALOADER_WORKERS", 4))  # as in Ark+ (FLIP#1434)
        self.train_loader = DataLoader(
            Dataset(train_items, transform=get_brain_mri_transforms()),
            batch_size=config["BATCH_SIZE"],
            shuffle=True,
            num_workers=workers,
        )
        self.val_loader = DataLoader(
            Dataset(val_items, transform=get_brain_mri_transforms(is_validation=True)),
            batch_size=config["BATCH_SIZE"],
            shuffle=False,
            num_workers=workers,
        )

    def build_datalist(self, dataframe: pd.DataFrame) -> tuple[list[dict], list[dict]]:
        """List each accession's volumes, one item per configured MR sequence, and split by accession.

        The four sequences of a study are four views of one brain, so whole studies go to training or
        to validation, never both.
        """
        modalities = self.config["MODALITIES"]
        by_accession: dict[str, list[dict]] = {}
        for accession_id in dataframe["accession_id"]:
            try:
                accession_folder_path = self.flip.get_by_accession_number(
                    self.project_id, accession_id, resource_type=[ResourceType.NIFTI]
                )
            except Exception as err:
                logger.info(f"Could not get image data folder path for {accession_id}: {err}")
                continue

            items = []
            for image in sorted(accession_folder_path.rglob("input_*.nii.gz")):
                modality = modality_of(image, modalities)
                if modality is None:  # a sequence this run leaves out
                    continue
                items.append(
                    {
                        "image": str(image),
                        "accession_id": str(accession_id),
                        "modality": modality,
                        "modality_index": modalities.index(modality),
                    }
                )
            if items:
                by_accession[str(accession_id)] = items

        found = sum(len(items) for items in by_accession.values())
        logger.info(f"Found {found} volume(s) across {len(by_accession)} accession(s), modalities {modalities}.")
        if not by_accession:
            raise RuntimeError(
                f"No volumes matched MODALITIES={modalities}. A volume's sequence is read from its "
                "file name (input_<modality>_<case>.nii.gz) or its XNAT scan folder (scans/<1-4>-*/) "
                "— check that the configured labels are the ones the data actually carries."
            )

        accessions = list(by_accession)
        val_accessions = accessions[: int(self.config["VAL_SPLIT"] * len(accessions))]
        train_accessions = accessions[len(val_accessions) :]
        return (
            [item for accession in train_accessions for item in by_accession[accession]],
            [item for accession in val_accessions for item in by_accession[accession]],
        )

    def load_weights(self, weights: dict[str, torch.Tensor]) -> None:
        """Load the global weights, keep the autoencoder frozen, and check it is the expected one."""
        self.model.load_state_dict(state_dict=weights)
        self.model.to(self.device)
        freeze_autoencoder(self.model)
        verify_autoencoder(self.model)

    def get_inferer(self) -> tuple[LatentDiffusionInferer, list[int]]:
        """The latent inferer and the latent shape (channels first), built on first use and then kept.

        Built once because its scale factor, when ``LATENT_SCALE_FACTOR`` is null, is derived from a
        batch: rebuilding it each round, or for validation, would train and score at different scales.
        """
        if self._inferer is None:
            inferer, spatial = build_inferer(
                self.model,
                self.noise_scheduler,
                self.config["spatial_shape"],
                next(iter(self.train_loader))["image"],
                self.device,
                self.config.get("LATENT_SCALE_FACTOR"),
            )
            self._inferer = inferer, [self.model.autoencoder.latent_channels, *spatial]
        return self._inferer

    def train_one_epoch(self, inferer: LatentDiffusionInferer, latent_shape: list[int]) -> float:
        """One pass over the training split; returns the mean loss."""
        self.model.diffusion_model.train()
        max_norm = grad_clip_norm(self.config)
        total = 0.0
        for batch in self.train_loader:
            self.optimizer.zero_grad(set_to_none=True)
            images = batch["image"].to(self.device)
            condition = batch_condition(batch, self.config, self.device)
            loss = noise_prediction_loss(self.model, inferer, self.noise_scheduler, images, condition, latent_shape)
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
        inferer, latent_shape = self.get_inferer()
        for epoch in range(self.epochs):
            step = global_round * self.epochs + epoch + 1
            lr = lr_at(step - 1, self.config)
            set_learning_rate(self.optimizer, lr)
            epoch_loss = self.train_one_epoch(inferer, latent_shape)
            logger.info(f"Epoch {epoch + 1} / {self.epochs}: loss {epoch_loss}")
            writer.add_scalar("Total loss DM@epoch", epoch_loss, global_step=step)
            writer.add_scalar("LR DM@epoch", lr, global_step=step)

            if due_for_validation(epoch, self.epochs, self.config):
                val_loss = score_split(
                    self.model, self.val_loader, inferer, self.noise_scheduler, latent_shape, self.config, self.device
                )
                logger.info(f"Validation loss: {val_loss}")
                writer.add_scalar("Validation loss DM@epoch", val_loss, global_step=step)
                sample_and_save(
                    self.model, inferer, self.noise_scheduler, latent_shape, self.config, self.device, step=step
                )

        return self.epochs * len(self.train_loader)

    def validate(self, writer: SummaryWriter) -> float:
        """One ``evaluate`` task: score the aggregated model on this site's held-out split, and sample."""
        inferer, latent_shape = self.get_inferer()
        val_loss = score_split(
            self.model, self.val_loader, inferer, self.noise_scheduler, latent_shape, self.config, self.device
        )
        sample_and_save(self.model, inferer, self.noise_scheduler, latent_shape, self.config, self.device)
        writer.add_scalar("Total loss DM (val)", val_loss, global_step=0)
        return val_loss


def to_torch_weights(input_model: flare.FLModel) -> dict[str, torch.Tensor]:
    return {k: torch.as_tensor(v) for k, v in input_model.params.items()}


def send_weight_diff(original_params: dict, model: torch.nn.Module, n_iterations: int) -> None:
    """Send the weight diff for a completed training round.

    Built one tensor at a time to avoid holding a second full copy of the model in RAM (mirrors
    ``flip.utils.get_model_weights_diff``). It covers the whole model; the ``KeepOnlyVars`` filter that
    ``AGGREGATE_ONLY_REGEX`` wires drops everything but ``diffusion_model.*`` before it is sent.
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

    trainer = LatentDiffusionTrainer(config, project_id=args.project_id, query=load_query())

    while flare.is_running():
        input_model = flare.receive()
        if input_model is None:
            break

        global_round = input_model.current_round or 0

        if flare.is_train():
            logger.info(f"[LDM trainer] received train task (round {global_round})")
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
