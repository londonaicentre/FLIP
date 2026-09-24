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

"""NVFLARE Client API script for the FLIP autoencoder (VAE) tutorial.

Trains a KL-regularised autoencoder and its patch discriminator on 3-D brain MRI, as an ordinary
single-stage FedAvg job: one ``train`` task and one ``validate`` task, dispatched on
``flare.is_train()`` / ``flare.is_evaluate()``.

Data loading fetches each accession's NIfTI series and reads them through ``transforms.py``. A brain
MRI study is four co-registered sequences (FLAIR, T1w, T1Gd, T2w), and **every one of them is a
training sample here**: the autoencoder is deliberately modality-agnostic, so that one autoencoder
compresses all four and the latent diffusion tutorial downstream can condition on which is which.
``MODALITIES`` in ``config.json`` is the switch — leave all four listed to train across sequences,
or set it to ``["T1w"]`` for the single-sequence form. Nothing here reads the conditioning label; it
is attached to each sample (see ``modality.py``) for the diffusion tutorial's benefit.

Because the samples within one study are four views of one brain, the train/validation split is by
**accession**, not by file. Splitting by file would put a subject's T1w in training and their T2w in
validation, and the reported SSIM would then be measuring memorisation.

This is the first half of what used to be the two-stage latent diffusion job, split out so an
autoencoder can be trained, scored and re-used on its own. The reconstruction/adversarial maths is
unchanged from that job's ``train_ae`` phase. The run's aggregated weights are what the latent
diffusion tutorial consumes as its frozen encoder — see that tutorial's
``process_tools/extract_autoencoder.py``.
"""

import argparse
import json
import logging
from pathlib import Path

import einops
import nibabel as nib
import numpy as np
import nvflare.client as flare
import torch
from debug_samples import due_for_plot, save_triplanar
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

logger = logging.getLogger(__name__)


# The perceptual loss is MedicalNet ResNet-10, a 3-D network, staged offline by
# `medicalnet_perceptual` — see that module for why MONAI's own `PerceptualLoss(...)` constructor
# cannot be used here (it builds its backbone through `torch.hub.load`, and FL apps never download
# at run time, FLIP#1206). Being genuinely 3-D, it scores the whole volume rather than sampled
# slices, so the 2-D slicing below applies only to the discriminator now.


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
    """Raise a report naming every non-finite tensor, rather than a bare "found NaN".

    The run is still fatal — a site that trains on a poisoned tensor is worse than one that stops,
    and silently zeroing the offending loss hides exactly the instability worth seeing. What this
    adds is the *evidence*: which tensor went bad, whether it was NaN or inf, and the ranges of the
    others at the same step. A bare "NaN in reconstruction" cannot distinguish a diverging latent
    from an overflowing loss, and those want opposite fixes.
    """
    bad = [name for name, tensor in tensors.items() if not bool(torch.isfinite(tensor).all())]
    if not bad:
        return
    report = "\n  ".join(describe(name, tensor) for name, tensor in tensors.items())
    raise RuntimeError(f"Non-finite {', '.join(bad)} during {context}:\n  {report}")


class KLDivergenceLoss:
    """
    Expects z_mu (B, *latent_shape) and z_sigma (B, *latent_shape).
    Returns per-batch mean KL (averaged over batch).

    Computed in float32 even when the caller is inside ``autocast``. The autoencoder hands back
    fp16 tensors there, and this expression squares them: fp16 tops out at 65504, so any sigma
    above ~256 overflows to ``inf`` — and ``z_mu**2 + inf - log(inf) - 1`` is ``inf - inf``, i.e.
    **NaN**. That NaN reaches the weights and the next forward produces a NaN reconstruction, which
    the training loop can only report as "NaN in autoencoder reconstruction" a step after the real
    event. Early in training sigma genuinely does reach that range (observed above 2e4 on 3-D brain
    MRI within ~15 steps), so this is a live failure, not a theoretical one. The cast costs one
    copy of the latent, which is small next to the volumes themselves.
    """

    def __call__(
        self,
        z_mu: torch.Tensor,
        z_sigma: torch.Tensor,
    ) -> torch.Tensor:
        # We remove spatial notion
        z_mu_m = z_mu.float().flatten(start_dim=2)
        z_sigma_m = z_sigma.float().flatten(start_dim=2)
        kl_loss = 0.5 * torch.sum(z_mu_m.pow(2) + z_sigma_m.pow(2) - torch.log(z_sigma_m.pow(2)) - 1, dim=[-1])
        kl_loss = torch.sum(kl_loss) / kl_loss.shape[0]

        return kl_loss


# ---------------------------------------------------------------------------
# 3-D helpers for input 3D images
# ---------------------------------------------------------------------------


def detect_axial_anisotropy(image_path: str, threshold: float = 1.5) -> bool:
    """Whether a NIfTI volume is thick-slice, i.e. much coarser through-plane than in-plane.

    Anisotropy decides how a 3-D autoencoder should be scored: on thick-slice data a 3-D perceptual
    loss compares voxels that are not comparable, so the 2-D sliced form is the better signal (see
    :meth:`AutoencoderTrainer.reset_perceptual_to_anisotropic`).

    Not called by the shipped brain-MRI loader, on purpose. ``transforms.py`` resamples every volume
    onto an isotropic 96^3 grid, so by the time a tensor reaches a loss there is no through-plane
    axis to be coarse: the answer would always be False, and asking on the *raw* geometry instead
    (240x240x155, a ratio of 1.55) would log "found axial anisotropy" about data that is about to be
    made isotropic. Kept, and worth calling on the first readable volume, for a cohort that is
    genuinely thick-slice and resampled to a grid that preserves that.

    Args:
        image_path (str): Path to a NIfTI volume.
        threshold (float): In-plane to out-plane ratio above which the volume counts as anisotropic.

    Returns:
        bool: True when the volume is thick-slice.
    """
    volume = np.asarray(nib.load(str(image_path)).dataobj)
    in_plane_to_out_plane = volume.shape[0] / volume.shape[-1]
    if in_plane_to_out_plane > threshold:
        logger.info(f"Found axial anisotropy (in-plane to out-plane ratio is {in_plane_to_out_plane}).")
        return True
    return False


def slice_volume_for_2d(volume: torch.Tensor, slice_indices: np.ndarray) -> torch.Tensor:
    """Fold selected axial slices of a 3-D batch into a 2-D batch.

    ``(B, C, H, W, D) -> (B*len(slice_indices), C, H, W)``, so a 2-D perceptual loss or a 2-D
    discriminator can score a 3-D reconstruction slice by slice. Both operands of a comparison must be
    sliced with the SAME indices, or the loss compares unrelated anatomy.

    Args:
        volume (torch.Tensor): A 3-D batch, channel-first.
        slice_indices (np.ndarray): Indices along the last (through-plane) axis.

    Returns:
        torch.Tensor: The selected slices as a 2-D batch.
    """
    return einops.rearrange(volume[..., slice_indices], "b c h w d -> (b d) c h w")


# ---------------------------------------------------------------------------
# Utils
# ---------------------------------------------------------------------------


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


def split_for_site(items: list[dict], site_name: str) -> list[dict]:
    """Give each simulated site half the cohort, splitting on accession rather than on items.

    Local/simulator runs point every client at the same DEV dataset, so without this both sites
    train on identical data; in production each trust's data-access API already scopes the cohort to
    its own. The NVFLARE simulator names its clients "site-1"/"site-2" — normalise so the split
    actually applies (as arkplus_fine_tuning's data_utils does). Production trust names never match,
    so real runs are returned unsplit.

    Splitting on the *accession* keeps a study's sequences together: halving the item list would cut
    through the middle of a study and give one site a brain's T1w and the other its T2w.
    """
    normalised = site_name.replace("-", "")
    if normalised not in ("site1", "site2"):
        return items
    accessions = list(dict.fromkeys(item["accession_id"] for item in items))
    half = len(accessions) // 2
    mine = set(accessions[:half] if normalised == "site1" else accessions[half:])
    return [item for item in items if item["accession_id"] in mine]


def batch_accumulation_step(batch_size: int) -> int:
    """Accumulate gradients up to an effective batch of 8 when the configured batch is smaller."""
    if batch_size < 8:
        return 8 // batch_size
    return 1


# How often to hand cached CUDA blocks back to the driver. See the training loop for why this is
# not every iteration.
CACHE_CLEAR_EVERY = 50

SSIM_KERNEL_SIZE = 11
SSIM_KERNEL_SIGMA = 1.5


def foreground_ssim(
    reconstruction: torch.Tensor,
    images: torch.Tensor,
    kernel_size: int = SSIM_KERNEL_SIZE,
    kernel_sigma: float = SSIM_KERNEL_SIGMA,
) -> float:
    """Mean SSIM between a reconstruction and its input, over the **foreground only**.

    Background is excluded because it would otherwise decide the score. These volumes are
    skull-stripped, so everything outside the brain is one constant value — roughly 90% of a 96^3
    grid. Both the input and any reconstruction are near that constant there, SSIM is ~1 over all
    of it, and the reported number ends up measuring how much air the volume contains rather than
    how well the brain came back. The unmasked metric on this cohort reads ~0.72 after one round,
    when the reconstructions are visibly blobs.

    ``data_range`` is derived per volume rather than assumed. It sets SSIM's two stability
    constants (``c1 = (k1 * data_range)^2``, likewise ``c2``), so a wrong value silently rescales
    the whole metric. It was hard-coded to 1 while the chain ended in ``ScaleIntensityd(0, 1)``;
    ``NormalizeIntensityd`` z-scores each volume instead, giving a span nearer 5.5, and each volume
    gets its own because each is z-scored independently. The callers correspondingly pass the
    reconstruction **unclamped** — clamping it to [0, 1] flattened every voxel above 1, some 15% of
    the volume and all of the bright anatomy, to a constant. Together those two stale assumptions
    scored a *perfect* reconstruction at 0.26 rather than 1.0.

    Two details make the mask exact rather than approximate:

    * ``compute_ssim_and_cs`` returns the **full SSIM map**, not a scalar, so it can be masked at all
      — but it is a *valid* convolution, so the map is ``kernel_size - 1`` smaller per spatial axis
      (96 -> 86). The mask is therefore cropped by ``(kernel_size - 1) // 2`` on each side, which
      lines each map voxel up with the window centred on it.
    * It returns ``(ssim, contrast_sensitivity)`` **in that order**. Taking the second element gives
      contrast sensitivity — one factor of SSIM, not SSIM — which reads higher and moves less.

    Args:
        reconstruction (torch.Tensor): Reconstructed batch, channel-first, on the inputs' own
            intensity scale — do not clamp or renormalise it before passing it in.
        images (torch.Tensor): The input batch it is compared against, channel-first.
        kernel_size (int): Gaussian window size per spatial axis.
        kernel_sigma (float): Gaussian window sigma per spatial axis.

    Returns:
        float: Mean SSIM over foreground voxels, or NaN if the batch is entirely background.
    """
    spatial_dims = len(images.shape[2:])
    per_volume = tuple(range(1, images.ndim))
    # Shape (B, 1, ..., 1), which broadcasts against the SSIM map so each volume is scored on its
    # own range. Taken from the target, per convention: the reference defines the dynamic range.
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
    # The background is the volume's minimum, not zero: the transform chain z-scores each volume,
    # which maps the skull-stripped zeros to a negative constant. An affine map is monotonic, so the
    # originally-zero background is still exactly the minimum. (Measured against the pre-transform
    # mask on the shipped cohort: identical bar one voxel in 884,736, a float32 tie.)
    background = images.amin(dim=per_volume, keepdim=True)
    foreground = images[centres] > background
    if not bool(foreground.any()):
        logger.warning("SSIM: this batch is entirely background; reporting NaN rather than a score over nothing.")
        return float("nan")
    return ssim_map[foreground].mean().item()


class AutoencoderTrainer:
    """Holds the model, losses, optimizers, and data for autoencoder training.

    One instance lives for the whole ``flare.is_running()`` loop, so the optimizer state persists
    across global rounds.
    """

    def __init__(self, config: dict, project_id: str, query: str):
        self.config = config
        self.project_id = project_id
        working_dir = Path(__file__).parent.resolve()

        # Training parameters for the autoencoder
        self.params_autoencoder = {
            "lr_g": config["LR_G"],
            "lr_d": config["LR_D"],
            "epochs": config.get("LOCAL_ROUNDS", 5),
        }

        # Model creation
        self.model = get_model()
        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

        # Losses, optimizers etc.
        self.losses_ae = {
            "reconstruction_loss": torch.nn.L1Loss(),
            "kld_loss": KLDivergenceLoss(),
            "gan_loss": PatchAdversarialLoss(criterion="least_squares"),
            "perceptual_loss": load_medicalnet_perceptual(working_dir),
        }

        # 3-D only tu suppport 2D adversarial / perceptual losses.
        self.perceptual_slices = 12
        self.axial_anisotropy: bool | None = None
        # Reads the SAME key models.py builds the networks from (the top-level one), not the copy
        # inside stage_1: nothing constructs a network from that copy, so gating on it lets a 3-D
        # port flip one and not the other and get a 3-D network with the slicing paths switched off.
        self.is_volumetric = config["net_config"]["spatial_dims"] == 3
        self.weights_ae = {
            "w_reconstruction_loss": config["w_reconstruction_loss"],
            "w_perceptual_loss": config["w_perceptual_loss"],
            "w_kl_loss": config["w_kl_loss"],
            "w_gan_loss": config["w_gan_loss"],
        }

        self.optimizers_ae = {
            "optimizer_g": torch.optim.AdamW(
                self.model.autoencoder.parameters(), lr=self.params_autoencoder["lr_g"], weight_decay=1e-5
            ),
            "optimizer_d": torch.optim.AdamW(
                self.model.discriminator.parameters(),
                lr=self.params_autoencoder["lr_d"],
                weight_decay=1e-5,
            ),
        }

        # Exponential decay from LR_G to LR_END over the local epochs of one round, as the reference
        # MSD recipe does. With LR_END equal to LR_G the gamma is exactly 1 and the schedule is inert
        # — which is the shipped configuration, so the decay is opt-in by lowering LR_END.
        epochs = max(1, self.params_autoencoder["epochs"])
        lr_end = config.get("LR_END", self.params_autoencoder["lr_g"])
        gamma = (lr_end / self.params_autoencoder["lr_g"]) ** (1.0 / epochs)
        self.scheduler_g = torch.optim.lr_scheduler.ExponentialLR(self.optimizers_ae["optimizer_g"], gamma=gamma)

        # Data loading
        self.flip = FLIP()
        dataframe = self.flip.get_dataframe(project_id=project_id, query=query)
        self.train_items, self.val_items = self.build_datalist(dataframe)

        # Per-site split for local/simulator runs where every client reads the same DEV dataset;
        # in production each trust's data-access API already scopes the cohort to its own data.
        # The NVFLARE simulator names its clients "site-1"/"site-2"; normalise so the split actually applies
        # (as arkplus_fine_tuning's data_utils does). Production trust names never match, so real runs are unsplit.
        self.site_name = flare.get_site_name()
        self.train_items = split_for_site(self.train_items, self.site_name)

        self._train_dataset = Dataset(self.train_items, transform=get_brain_mri_transforms())
        self._val_dataset = Dataset(self.val_items, transform=get_brain_mri_transforms(is_validation=True))

    def build_datalist(self, dataframe) -> tuple[list, list]:
        """Fetch each accession's NIfTI series and split into train/validation lists.

        One study yields one sample per configured modality — four, unless ``MODALITIES`` narrows
        the run to fewer. Each sample carries the modality label and its index in ``MODALITIES``
        alongside the path; ``transforms.py`` passes both through untouched, and the collate turns
        the index into a batch tensor.

        **The split is by accession.** The four series of one study are four views of the same
        brain, so a file-wise split would leak a subject across the boundary and make the validation
        score meaningless. Whole studies go to one side or the other.
        """
        modalities = self.config["MODALITIES"]
        by_accession: dict[str, list[dict]] = {}

        for accession_id in dataframe["accession_id"]:
            try:
                accession_folder_path = self.flip.get_by_accession_number(
                    self.project_id,
                    accession_id,
                    resource_type=[
                        ResourceType.NIFTI,
                    ],
                )
            except Exception as err:
                logger.info(f"Could not get image data folder path for {accession_id}: {err}")
                continue

            items = []
            for image in sorted(accession_folder_path.rglob("input_*.nii.gz")):
                modality = modality_of(image, modalities)
                if modality is None:
                    # A sequence this run excludes, not an error: MODALITIES is the cohort filter.
                    continue
                items.append({
                    "image": str(image),
                    "accession_id": str(accession_id),
                    "modality": modality,
                    "modality_index": modalities.index(modality),
                })
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

        # Validation / train splits, whole accessions at a time.
        accessions = list(by_accession)
        val_accessions = accessions[: int(self.config["VAL_SPLIT"] * len(accessions))]
        train_accessions = accessions[len(val_accessions) :]
        return (
            [item for accession in train_accessions for item in by_accession[accession]],
            [item for accession in val_accessions for item in by_accession[accession]],
        )

    def reset_perceptual_to_anisotropic(self):
        """Swap the perceptual loss for its 2-D form when 3-D data turns out to be thick-slice.

        3-D only, and not called by the shipped 2-D pipeline: a 3-D port's loader should call this once
        :func:`detect_axial_anisotropy` returns True (setting ``self.axial_anisotropy`` first). The
        weight is raised alongside the swap because this network reports much smaller values.
        """
        if self.axial_anisotropy:
            # The perceptual loss stays 3-D regardless: MedicalNet has no 2-D counterpart we can
            # ship offline, and anisotropic spacing degrades its features rather than breaking
            # them. Only the discriminator falls back to scoring slices.
            logger.info("Axial anisotropy detected; the 2-D discriminator scores slices, perceptual stays 3-D.")

    def make_loaders(self, batch_size: int, shuffle: bool = True) -> tuple[DataLoader, DataLoader]:
        train_loader = DataLoader(self._train_dataset, batch_size=batch_size, shuffle=shuffle, num_workers=1)
        val_loader = DataLoader(self._val_dataset, batch_size=batch_size, shuffle=shuffle, num_workers=1)
        return train_loader, val_loader

    def val_loader(self, batch_size: int) -> DataLoader:
        return DataLoader(self._val_dataset, batch_size=batch_size, shuffle=False, num_workers=1)

    def load_weights(self, weights: dict[str, torch.Tensor]) -> None:
        self.model.load_state_dict(state_dict=weights, strict=False)

    def train(self, writer: SummaryWriter, global_round: int) -> int:
        """One ``train`` round: local AE + discriminator epochs. Returns the iteration count."""
        accumulation_step = batch_accumulation_step(self.config["BATCH_SIZE"])
        train_loader, val_loader = self.make_loaders(self.config["BATCH_SIZE"])
        self.model.autoencoder.to(device=self.device)
        self.model.discriminator.to(device=self.device)
        self.losses_ae["perceptual_loss"].to(self.device)

        # Create GradScalers - ensure they don't accumulate state
        scaler_g = GradScaler(enabled=True)
        scaler_d = GradScaler(enabled=True)

        # Basic training
        self.model.autoencoder.train()
        self.model.discriminator.train()
        epochs = self.params_autoencoder["epochs"]
        for epoch in range(epochs):
            train_g_loss = 0
            train_d_loss = 0
            train_g_l1loss = 0
            train_g_percloss = 0
            train_g_ganloss = 0
            train_g_klloss = 0

            batch_acc_counter = 0
            for ind, batch in enumerate(train_loader):
                images = batch["image"].to(self.device)

                # 3-D only: pick the axial slices a 2-D loss/discriminator will score. Both operands of
                # each comparison below reuse these same indices. Inert for 2-D data.
                slice_indices = (
                    np.random.randint(0, images.shape[-1], size=self.perceptual_slices) if self.is_volumetric else None
                )

                # TRAIN GENERATOR
                self.model.discriminator.requires_grad_(False)
                with autocast(enabled=True, device_type=self.device.type):
                    reconstruction, z_mu, z_sigma = self.model.autoencoder(images)
                    require_finite(
                        {"reconstruction": reconstruction, "z_mu": z_mu, "z_sigma": z_sigma},
                        f"the autoencoder forward pass (epoch {epoch + 1}, iteration {ind})",
                    )
                    kl_loss = self.weights_ae["w_kl_loss"] * self.losses_ae["kld_loss"](z_mu, z_sigma)
                    l1_loss = self.weights_ae["w_reconstruction_loss"] * self.losses_ae["reconstruction_loss"](
                        reconstruction.float(), images.float()
                    )
                    # A 2-D perceptual loss on 3-D data scores slices; a 2-D image is passed straight in.
                    if self.is_volumetric and self.losses_ae["perceptual_loss"].spatial_dims == 2:
                        p_loss = self.losses_ae["perceptual_loss"](
                            slice_volume_for_2d(reconstruction, slice_indices).float(),
                            slice_volume_for_2d(images, slice_indices).float(),
                        )
                    else:
                        p_loss = self.losses_ae["perceptual_loss"](reconstruction.float(), images.float())
                    p_loss = self.weights_ae["w_perceptual_loss"] * p_loss

                    # Likewise for a 2-D discriminator judging a 3-D reconstruction.
                    if self.is_volumetric and self.config["net_config"]["discriminator"]["spatial_dims"] == 2:
                        logits_fake = self.model.discriminator(
                            slice_volume_for_2d(reconstruction, slice_indices).contiguous().float()
                        )[-1]
                    else:
                        logits_fake = self.model.discriminator(reconstruction.contiguous().float())[-1]

                    gan_loss = self.weights_ae["w_gan_loss"] * self.losses_ae["gan_loss"](
                        logits_fake, target_is_real=True, for_discriminator=False
                    )

                    require_finite(
                        {"l1": l1_loss, "kl": kl_loss, "perceptual": p_loss, "gan": gan_loss},
                        f"the generator loss (epoch {epoch + 1}, iteration {ind})",
                    )

                    # Loss generator
                    train_g_loss_ = l1_loss + kl_loss + p_loss + gan_loss
                    # print(
                    #    f"KL: {kl_loss.item()}, l1: {l1_loss.item()}, "
                    #    f"perceptual: {p_loss.item()}, gan: {gan_loss.item()}"
                    # )

                scaler_g.scale(train_g_loss_ / accumulation_step).backward()
                self.model.discriminator.requires_grad_(True)

                batch_acc_counter += 1
                if batch_acc_counter % accumulation_step == 0 or ind == (len(train_loader) - 1):
                    scaler_g.step(self.optimizers_ae["optimizer_g"])
                    scaler_g.update()
                    self.optimizers_ae["optimizer_g"].zero_grad(set_to_none=True)

                global_step = (global_round * epochs + epoch) * len(train_loader) + ind
                if due_for_plot(global_step, self.config):
                    save_triplanar(
                        {"input": images, "reconstruction": reconstruction},
                        "triplanar",
                        self.config,
                        site_name=self.site_name,
                        step=global_step,
                    )

                del z_mu, z_sigma, logits_fake

                # TRAIN DISCRIMINATOR
                if self.is_volumetric and self.config["net_config"]["discriminator"]["spatial_dims"] == 2:
                    logits_fake = self.model.discriminator(
                        slice_volume_for_2d(reconstruction.detach(), slice_indices).contiguous().float()
                    )[-1]
                    logits_real = self.model.discriminator(
                        slice_volume_for_2d(images, slice_indices).contiguous().float()
                    )[-1]
                else:
                    logits_real = self.model.discriminator(images.float())[-1]
                    logits_fake = self.model.discriminator(reconstruction.detach().contiguous().float())[-1]

                loss_d_fake = self.losses_ae["gan_loss"](logits_fake, target_is_real=False, for_discriminator=True)
                loss_d_real = self.losses_ae["gan_loss"](logits_real, target_is_real=True, for_discriminator=True)
                d_loss = self.weights_ae["w_gan_loss"] * (loss_d_fake + loss_d_real) * 0.5
                scaler_d.scale(d_loss / accumulation_step).backward()

                if batch_acc_counter % accumulation_step == 0 or ind == (len(train_loader) - 1):
                    scaler_d.step(self.optimizers_ae["optimizer_d"])
                    scaler_d.update()
                    self.optimizers_ae["optimizer_d"].zero_grad(set_to_none=True)

                del logits_real, logits_fake, reconstruction

                # Aggregate
                train_g_loss += train_g_loss_.item()
                train_g_ganloss += gan_loss.item()
                train_g_percloss += p_loss.item()
                train_g_l1loss += l1_loss.item()
                train_g_klloss += kl_loss.item()
                train_d_loss += d_loss.item()

                del train_g_loss_, gan_loss, p_loss, l1_loss, kl_loss, d_loss, images

                if torch.cuda.is_available() and ind % CACHE_CLEAR_EVERY == 0:
                    torch.cuda.empty_cache()

            # Aggregate at the end
            train_g_loss /= max(1, len(train_loader))
            train_g_ganloss /= max(1, len(train_loader))
            train_g_percloss /= max(1, len(train_loader))
            train_g_l1loss /= max(1, len(train_loader))
            train_g_klloss /= max(1, len(train_loader))
            train_d_loss /= max(1, len(train_loader))

            # Validation
            val_loss = 0
            val_ssim = 0.0
            debug_batch: dict[str, torch.Tensor] = {}
            self.model.autoencoder.eval()
            for batch in val_loader:
                images = batch["image"].to(self.device)
                with autocast(enabled=True, device_type=self.device.type):
                    with torch.no_grad():
                        reconstruction, _, _ = self.model.autoencoder(images)
                    if True in torch.isnan(reconstruction):
                        break
                    if not debug_batch:
                        debug_batch = {"input": images, "reconstruction": reconstruction}
                    val_loss += (
                        self.weights_ae["w_reconstruction_loss"]
                        * self.losses_ae["reconstruction_loss"](reconstruction.float(), images.float()).item()
                    )

                val_ssim += foreground_ssim(reconstruction.float(), images.float())

            val_loss /= max(1, len(val_loader))
            val_ssim /= max(1, len(val_loader))
            self.model.autoencoder.train()

            logger.info(
                f"Epoch {epoch + 1} / {epochs};\n "
                f"Total loss G: {train_g_loss}, GAN: {train_g_ganloss} "
                f"Perceptual: {train_g_percloss}, L1: {train_g_l1loss} "
                f"KLD: {train_g_klloss} \n"
                f"Total loss D: {train_d_loss},Validation loss (L1): {val_loss}, "
                f"Validation SSIM (foreground): {val_ssim}"
            )

            # Send metrics to FLIP. Labels match the legacy tutorial's series names; the "@epoch"
            # suffix names the x-axis and `step` (cumulative local epoch) is the coordinate.
            self.scheduler_g.step()

            step = global_round * epochs + epoch + 1
            writer.add_scalar("Train loss (G)@epoch", train_g_loss, global_step=step)
            writer.add_scalar("Train loss (D)@epoch", train_d_loss, global_step=step)
            writer.add_scalar("Perceptual loss (G)@epoch", train_g_percloss, global_step=step)
            writer.add_scalar("KLD loss (G)@epoch", train_g_klloss, global_step=step)
            writer.add_scalar("Reconstruction loss (G)@epoch", train_g_l1loss, global_step=step)
            writer.add_scalar("GAN loss (G)@epoch", train_g_ganloss, global_step=step)
            writer.add_scalar("Validation loss (L1)@epoch", val_loss, global_step=step)
            # Foreground-only, so it is comparable with the `val_ssim` the validate task reports and
            # is not dominated by the ~90% of each volume that is background.
            writer.add_scalar("Validation SSIM@epoch", val_ssim, global_step=step)

            save_triplanar(debug_batch, "reconstruction", self.config, site_name=self.site_name, step=step)

        return epochs * len(train_loader)


def validate(
    model: torch.nn.Module,
    test_loader: DataLoader,
    device: torch.device,
    writer: SummaryWriter,
    config: dict,
) -> tuple[float, float]:
    """Score the aggregated autoencoder on the local held-out split.

    Args:
        model (torch.nn.Module): The autoencoder network with the broadcast global weights loaded.
        test_loader (DataLoader): Held-out data loader.
        device (torch.device): Device to run on.
        config (dict): The user app config (``config.json``); read only for the debug-sample flag.
        writer (SummaryWriter): NVFLARE Client API metrics writer (relayed to FLIP by the
            analytics bridge).

    Returns:
        tuple[float, float]: ``(l1_test_loss, ssim_test)`` averaged over the held-out split. The
        SSIM is foreground-only — see :func:`foreground_ssim`.
    """
    reconstruction_loss = torch.nn.L1Loss()
    model.autoencoder.to(device=device)
    model.eval()

    l1_test_loss = 0.0
    ssim_test = 0.0
    debug_batch: dict[str, torch.Tensor] = {}

    for batch in test_loader:
        images = batch["image"].to(device)

        with torch.no_grad():
            reconstruction, _, _ = model.autoencoder(images)
        if not debug_batch:
            debug_batch = {"input": images, "reconstruction": reconstruction}
        reconstruction = reconstruction.detach().cpu().float()
        images = images.detach().cpu().float()

        ssim_test += foreground_ssim(reconstruction, images)
        l1_test_loss += reconstruction_loss(reconstruction, images).item()

    l1_test_loss /= max(1, len(test_loader))
    ssim_test /= max(1, len(test_loader))

    logger.info(f"Validation loss: {l1_test_loss} SSIM: {ssim_test}")

    writer.add_scalar("val_l1_loss", l1_test_loss, global_step=0)
    writer.add_scalar("val_ssim", ssim_test, global_step=0)

    save_triplanar(debug_batch, "reconstruction_aggregated", config, site_name=flare.get_site_name())

    return l1_test_loss, ssim_test


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

        weights = to_torch_weights(input_model)
        global_round = input_model.current_round or 0

        if flare.is_train():
            logger.info(f"[autoencoder trainer] received train task (round {global_round})")
            trainer.load_weights(weights)
            n_iterations = trainer.train(writer, global_round)
            send_weight_diff(input_model.params, trainer.model, n_iterations)

        elif flare.is_evaluate():
            trainer.model.load_state_dict({k: v.to(trainer.device) for k, v in weights.items()})
            test_loader = trainer.val_loader(config["BATCH_SIZE"])
            val_loss, val_ssim = validate(trainer.model, test_loader, trainer.device, writer, config)
            logger.info(f"Validating the aggregated autoencoder on {flare.get_site_name()}'s data: SSIM {val_ssim}")
            # val_acc lands in the cross-validation results.json via ValidationJsonGenerator.
            flare.send(flare.FLModel(metrics={"val_acc": val_ssim, "val_loss": val_loss}))

        else:
            logger.warning("Received unknown task; ignoring.")


if __name__ == "__main__":
    main()
