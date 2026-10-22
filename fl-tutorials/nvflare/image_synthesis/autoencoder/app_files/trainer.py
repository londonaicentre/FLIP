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

"""FLARE Client API script for the FLIP autoencoder tutorial.

Trains a KL-regularised autoencoder (MONAI's ``AutoencoderKL``) and a patch discriminator on 3-D brain
MRI, as an ordinary FedAvg job. On a ``train`` task the client trains the received model for
``LOCAL_ROUNDS`` epochs and sends back the weight difference; on an ``evaluate`` task it scores the
aggregated model on its held-out split.

Every MR sequence in ``MODALITIES`` is a training sample, so one autoencoder serves all of them; the
latent diffusion tutorial reuses it, frozen. The generator loss is L1 + perceptual (MedicalNet, see
``medicalnet_perceptual.py``) + KL + adversarial, under mixed precision, with gradients accumulated
to an effective batch of ``EFFECTIVE_BATCH_SIZE``. Validation reports L1 and foreground SSIM.
"""

import argparse
import json
import logging
import math
from pathlib import Path

import einops
import numpy as np
import nvflare.client as flare
import torch
from flip import FLIP
from flip.constants import ResourceType
from medicalnet_perceptual import load_medicalnet_perceptual
from modality import modality_of
from models import get_model
from monai.data import DataLoader, Dataset
from monai.losses import PatchAdversarialLoss
from monai.metrics import compute_ssim_and_cs
from nvflare.client.tracking import SummaryWriter
from torch.amp import GradScaler, autocast
from transforms import get_brain_mri_transforms

# plot_utils.py is a local-development helper (../dev/): job.py stages it into simulator runs, and it is
# never uploaded, so on a trust this import fails and debug output is simply off.
try:
    from plot_utils import due_for_plot, save_triplanar
except ModuleNotFoundError as err:
    if err.name != "plot_utils":
        raise

    def due_for_plot(iteration: int, config: dict) -> bool:
        return False

    def save_triplanar(*args: object, **kwargs: object) -> None:
        return None


logger = logging.getLogger(__name__)

#: Gradients are accumulated up to this many samples per optimiser step.
EFFECTIVE_BATCH_SIZE = 8

#: AdamW weight decay for both networks.
WEIGHT_DECAY = 1e-5

#: Axial slices a 2-D discriminator scores per training step.
DISCRIMINATOR_SLICES = 12

#: Batches between releases of cached CUDA memory, against fragmentation; every batch would stall.
CACHE_CLEAR_EVERY = 50

SSIM_KERNEL_SIZE = 11
SSIM_KERNEL_SIGMA = 1.5


def describe(name: str, tensor: torch.Tensor) -> str:
    """One-line summary of a tensor's finiteness and range, for a failure report."""
    finite = torch.isfinite(tensor)
    n_nan = int(torch.isnan(tensor).sum())
    n_inf = int(torch.isinf(tensor).sum())
    if bool(finite.any()):
        lo, hi = tensor[finite].min().item(), tensor[finite].max().item()
        span = f"finite range [{lo:.4g}, {hi:.4g}]"
    else:
        span = "no finite values"
    return f"{name}: nan={n_nan} inf={n_inf} of {tensor.numel()} elements, {span}"


def require_finite(tensors: dict, context: str) -> None:
    """Stop the run if any tensor is not finite, reporting which ones and the ranges of all of them."""
    bad = [name for name, tensor in tensors.items() if not bool(torch.isfinite(tensor).all())]
    if not bad:
        return
    report = "\n  ".join(describe(name, tensor) for name, tensor in tensors.items())
    raise RuntimeError(f"Non-finite {', '.join(bad)} during {context}:\n  {report}")


class KLDivergenceLoss:
    """KL divergence of the latent from a unit Gaussian, summed over the latent, averaged over the batch.

    Computed in float32 even inside ``autocast``: squaring an fp16 sigma overflows above ~256, which
    happens early in training on this cohort, and turns the loss into NaN.
    """

    def __call__(self, z_mu: torch.Tensor, z_sigma: torch.Tensor) -> torch.Tensor:
        z_mu = z_mu.float().flatten(start_dim=2)
        z_sigma = z_sigma.float().flatten(start_dim=2)
        kl_loss = 0.5 * torch.sum(z_mu.pow(2) + z_sigma.pow(2) - torch.log(z_sigma.pow(2)) - 1, dim=[-1])
        return torch.sum(kl_loss) / kl_loss.shape[0]


def slice_volume_for_2d(volume: torch.Tensor, slice_indices: np.ndarray) -> torch.Tensor:
    """Fold chosen axial slices of a 3-D batch into a 2-D batch, ``(B, C, H, W, D) -> (B*n, C, H, W)``.

    Lets a 2-D discriminator score a 3-D volume. Slice both operands of a comparison with the same
    indices.
    """
    return einops.rearrange(volume[..., slice_indices], "b c h w d -> (b d) c h w")


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


def batch_accumulation_step(batch_size: int) -> int:
    """Batches to accumulate per optimiser step, reaching ``EFFECTIVE_BATCH_SIZE`` samples."""
    return max(1, EFFECTIVE_BATCH_SIZE // batch_size)


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


def foreground_ssim(
    reconstruction: torch.Tensor,
    images: torch.Tensor,
    kernel_size: int = SSIM_KERNEL_SIZE,
    kernel_sigma: float = SSIM_KERNEL_SIGMA,
) -> float:
    """Mean SSIM between a reconstruction and its input, over the brain only.

    The skull-stripped background is about 80% of the grid and scores ~1 for any reconstruction, so
    it is masked out: the background is each input volume's minimum, since the chain z-scores the
    zeros to a negative constant. ``data_range`` is taken per volume from the input, so pass the
    reconstruction on the same z-scored scale, unclamped. ``compute_ssim_and_cs`` returns the full SSIM
    map, ``kernel_size - 1`` smaller per axis, hence the cropped mask; its first output is SSIM, the
    second contrast sensitivity.

    Returns:
        float: Mean SSIM over foreground voxels, or NaN if the batch is entirely background.
    """
    spatial_dims = len(images.shape[2:])
    per_volume = tuple(range(1, images.ndim))
    data_range = images.amax(dim=per_volume, keepdim=True) - images.amin(dim=per_volume, keepdim=True)
    ssim_map, _ = compute_ssim_and_cs(
        reconstruction,
        images,
        spatial_dims=spatial_dims,
        data_range=data_range,
        kernel_size=[kernel_size] * spatial_dims,
        kernel_sigma=[kernel_sigma] * spatial_dims,
    )

    trim = (kernel_size - 1) // 2
    centres = (slice(None), slice(None)) + tuple(slice(trim, size - trim) for size in images.shape[2:])
    background = images.amin(dim=per_volume, keepdim=True)
    foreground = images[centres] > background
    if not bool(foreground.any()):
        logger.warning("SSIM: this batch is entirely background; reporting NaN rather than a score over nothing.")
        return float("nan")
    return ssim_map[foreground].mean().item()


def validate(model: torch.nn.Module, loader: DataLoader, device: torch.device) -> tuple[float, float, dict]:
    """Score the autoencoder on a held-out split, under the same autocast as training.

    Returns:
        tuple[float, float, dict]: The mean L1 loss and mean foreground SSIM (see
        :func:`foreground_ssim`) over the split, and its first batch for the debug plots.
    """
    model.autoencoder.eval()
    l1_loss, ssim, first_batch = 0.0, 0.0, {}
    for batch in loader:
        images = batch["image"].to(device)
        with torch.no_grad(), autocast(enabled=True, device_type=device.type):
            reconstruction, _, _ = model.autoencoder(images)
        require_finite({"reconstruction": reconstruction}, "validation")
        if not first_batch:
            first_batch = {"input": images, "reconstruction": reconstruction}
        l1_loss += torch.nn.functional.l1_loss(reconstruction.float(), images.float()).item()
        ssim += foreground_ssim(reconstruction.float(), images.float())
    return l1_loss / max(1, len(loader)), ssim / max(1, len(loader)), first_batch


class AutoencoderTrainer:
    """The networks, losses, optimisers and data for autoencoder training."""

    def __init__(self, config: dict, project_id: str, query: str):
        self.config = config
        self.project_id = project_id
        self.epochs = config["LOCAL_ROUNDS"]

        self.model = get_model()
        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        self.perceptual_loss = load_medicalnet_perceptual(Path(__file__).parent.resolve()).to(self.device)
        self.adversarial_loss = PatchAdversarialLoss(criterion="least_squares")
        self.kl_loss = KLDivergenceLoss()
        self.w_reconstruction = config["W_RECONSTRUCTION"]
        self.w_perceptual = config["W_PERCEPTUAL"]
        self.w_kl = config["W_KL"]
        self.w_gan = config["W_GAN"]
        # A 2-D discriminator judges a 3-D volume slice by slice.
        net_config = config["net_config"]
        self.slice_discriminator = net_config["spatial_dims"] == 3 and net_config["discriminator"]["spatial_dims"] == 2

        self.optimizer_g = torch.optim.AdamW(
            self.model.autoencoder.parameters(), lr=config["LR_START"], weight_decay=WEIGHT_DECAY
        )
        self.optimizer_d = torch.optim.AdamW(
            self.model.discriminator.parameters(), lr=config["LR_START"], weight_decay=WEIGHT_DECAY
        )

        # Data
        self.flip = FLIP()
        self.site_name = flare.get_site_name()
        train_items, val_items = self.build_datalist(self.flip.get_dataframe(project_id=project_id, query=query))
        train_items = split_for_site(train_items, self.site_name)
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

    def build_datalist(self, dataframe) -> tuple[list[dict], list[dict]]:
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
                f"No volumes matched MODALITIES={modalities}. The cohort's files are named "
                "input_<modality>_<case>.nii.gz — check that the configured labels are the ones "
                "the data actually carries."
            )

        accessions = list(by_accession)
        val_accessions = accessions[: int(self.config["VAL_SPLIT"] * len(accessions))]
        train_accessions = accessions[len(val_accessions) :]
        return (
            [item for accession in train_accessions for item in by_accession[accession]],
            [item for accession in val_accessions for item in by_accession[accession]],
        )

    def load_weights(self, weights: dict[str, torch.Tensor]) -> None:
        self.model.load_state_dict(state_dict=weights)
        self.model.to(self.device)

    def discriminator_logits(self, volume: torch.Tensor, slice_indices: np.ndarray) -> torch.Tensor:
        """The discriminator's patch logits for a volume, slice by slice when it is 2-D."""
        if self.slice_discriminator:
            volume = slice_volume_for_2d(volume, slice_indices)
        return self.model.discriminator(volume.contiguous().float())[-1]

    def train_one_epoch(self, epoch: int, global_round: int) -> dict[str, float]:
        """One pass over the training split, generator then discriminator per batch; returns mean losses."""
        self.model.autoencoder.train()
        self.model.discriminator.train()
        accumulation_step = batch_accumulation_step(self.config["BATCH_SIZE"])
        totals = dict.fromkeys(("generator", "discriminator", "l1", "perceptual", "kl", "gan"), 0.0)
        for ind, batch in enumerate(self.train_loader):
            images = batch["image"].to(self.device)
            slice_indices = np.random.randint(0, images.shape[-1], size=DISCRIMINATOR_SLICES)
            optimizer_step = (ind + 1) % accumulation_step == 0 or ind == len(self.train_loader) - 1

            # Generator: the discriminator is frozen so this backward leaves no gradients on it.
            self.model.discriminator.requires_grad_(False)
            with autocast(enabled=True, device_type=self.device.type):
                reconstruction, z_mu, z_sigma = self.model.autoencoder(images)
                require_finite(
                    {"reconstruction": reconstruction, "z_mu": z_mu, "z_sigma": z_sigma},
                    f"the autoencoder forward pass (epoch {epoch + 1}, iteration {ind})",
                )
                losses = {
                    "l1": self.w_reconstruction * torch.nn.functional.l1_loss(reconstruction.float(), images.float()),
                    "perceptual": self.w_perceptual * self.perceptual_loss(reconstruction.float(), images.float()),
                    "kl": self.w_kl * self.kl_loss(z_mu, z_sigma),
                    "gan": self.w_gan
                    * self.adversarial_loss(
                        self.discriminator_logits(reconstruction, slice_indices),
                        target_is_real=True,
                        for_discriminator=False,
                    ),
                }
                require_finite(losses, f"the generator loss (epoch {epoch + 1}, iteration {ind})")
                generator_loss = sum(losses.values())
            self.scaler_g.scale(generator_loss / accumulation_step).backward()
            self.model.discriminator.requires_grad_(True)
            if optimizer_step:
                self.scaler_g.step(self.optimizer_g)
                self.scaler_g.update()
                self.optimizer_g.zero_grad(set_to_none=True)

            iteration = (global_round * self.epochs + epoch) * len(self.train_loader) + ind
            if due_for_plot(iteration, self.config):
                save_triplanar(
                    {"input": images, "reconstruction": reconstruction},
                    "triplanar",
                    self.config,
                    site_name=self.site_name,
                    step=iteration,
                )

            # Discriminator: real volumes against the (detached) reconstructions.
            fake = self.adversarial_loss(
                self.discriminator_logits(reconstruction.detach(), slice_indices),
                target_is_real=False,
                for_discriminator=True,
            )
            real = self.adversarial_loss(
                self.discriminator_logits(images, slice_indices), target_is_real=True, for_discriminator=True
            )
            discriminator_loss = self.w_gan * (fake + real) * 0.5
            self.scaler_d.scale(discriminator_loss / accumulation_step).backward()
            if optimizer_step:
                self.scaler_d.step(self.optimizer_d)
                self.scaler_d.update()
                self.optimizer_d.zero_grad(set_to_none=True)

            totals["generator"] += generator_loss.item()
            totals["discriminator"] += discriminator_loss.item()
            for name, value in losses.items():
                totals[name] += value.item()
            if torch.cuda.is_available() and ind % CACHE_CLEAR_EVERY == 0:
                torch.cuda.empty_cache()

        return {name: total / max(1, len(self.train_loader)) for name, total in totals.items()}

    def train(self, writer: SummaryWriter, global_round: int) -> int:
        """One ``train`` task: ``LOCAL_ROUNDS`` epochs. Returns the number of batches trained on."""
        self.scaler_g, self.scaler_d = GradScaler(), GradScaler()
        for epoch in range(self.epochs):
            step = global_round * self.epochs + epoch + 1
            for optimizer in (self.optimizer_g, self.optimizer_d):
                set_learning_rate(optimizer, lr_at(step - 1, self.config))
            losses = self.train_one_epoch(epoch, global_round)
            val_loss, val_ssim, first_batch = validate(self.model, self.val_loader, self.device)

            logger.info(
                f"Epoch {epoch + 1} / {self.epochs};\n "
                f"Total loss G: {losses['generator']}, GAN: {losses['gan']} "
                f"Perceptual: {losses['perceptual']}, L1: {losses['l1']} "
                f"KLD: {losses['kl']} \n"
                f"Total loss D: {losses['discriminator']},Validation loss (L1): {val_loss}, "
                f"Validation SSIM (foreground): {val_ssim}"
            )
            # The "@epoch" suffix names the x-axis; `step` counts local epochs across rounds.
            writer.add_scalar("Train loss (G)@epoch", losses["generator"], global_step=step)
            writer.add_scalar("Train loss (D)@epoch", losses["discriminator"], global_step=step)
            writer.add_scalar("Perceptual loss (G)@epoch", losses["perceptual"], global_step=step)
            writer.add_scalar("KLD loss (G)@epoch", losses["kl"], global_step=step)
            writer.add_scalar("Reconstruction loss (G)@epoch", losses["l1"], global_step=step)
            writer.add_scalar("GAN loss (G)@epoch", losses["gan"], global_step=step)
            writer.add_scalar("Validation loss (L1)@epoch", val_loss, global_step=step)
            writer.add_scalar("Validation SSIM@epoch", val_ssim, global_step=step)
            save_triplanar(first_batch, "reconstruction", self.config, site_name=self.site_name, step=step)

        return self.epochs * len(self.train_loader)


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

    trainer = AutoencoderTrainer(config, project_id=args.project_id, query=load_query())

    while flare.is_running():
        input_model = flare.receive()
        if input_model is None:
            break

        global_round = input_model.current_round or 0

        if flare.is_train():
            logger.info(f"[autoencoder trainer] received train task (round {global_round})")
            trainer.load_weights(to_torch_weights(input_model))
            n_iterations = trainer.train(writer, global_round)
            send_weight_diff(input_model.params, trainer.model, n_iterations)

        elif flare.is_evaluate():
            trainer.load_weights(to_torch_weights(input_model))
            val_loss, val_ssim, first_batch = validate(trainer.model, trainer.val_loader, trainer.device)
            writer.add_scalar("val_l1_loss", val_loss, global_step=0)
            writer.add_scalar("val_ssim", val_ssim, global_step=0)
            save_triplanar(first_batch, "reconstruction_aggregated", config, site_name=trainer.site_name)
            logger.info(f"Validating the aggregated autoencoder on {trainer.site_name}'s data: SSIM {val_ssim}")
            # val_acc lands in the cross-validation results.json via ValidationJsonGenerator.
            flare.send(flare.FLModel(metrics={"val_acc": val_ssim, "val_loss": val_loss}))

        else:
            logger.warning("Received unknown task; ignoring.")


if __name__ == "__main__":
    main()
