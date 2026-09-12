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

Trains a KL-regularised autoencoder and its patch discriminator on 2-D chest X-rays, as an ordinary
single-stage FedAvg job: one ``train`` task and one ``validate`` task, dispatched on
``flare.is_train()`` / ``flare.is_evaluate()``.

Data loading follows the ``xray_classification`` tutorial: DICOM files fetched per accession and read
through the pinned ``PydicomReader`` chain in ``transforms.py``. The cohort's label columns are
ignored — a generative model needs only the pixels.

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
import pydicom
import torch
from debug_samples import save_grid
from flip import FLIP
from flip.constants import ResourceType
from models import get_model
from monai.data import DataLoader, Dataset
from monai.losses import PatchAdversarialLoss, PerceptualLoss
from monai.metrics import compute_ssim_and_cs
from nvflare.client.tracking import SummaryWriter
from torch.amp import GradScaler, autocast
from transforms import get_xray_transforms

logger = logging.getLogger(__name__)


class KLDivergenceLoss:
    """
    Expects z_mu (B, *latent_shape) and z_sigma (B, *latent_shape).
    Returns per-batch mean KL (averaged over batch).
    """

    def __call__(
        self,
        z_mu: torch.Tensor,
        z_sigma: torch.Tensor,
    ) -> torch.Tensor:
        # We remove spatial notion
        z_mu_m = z_mu.flatten(start_dim=2)
        z_sigma_m = z_sigma.flatten(start_dim=2)
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

    Not called by the shipped DICOM loader — a 2-D radiograph has no through-plane axis. A 3-D port's
    loader should call it on the first readable volume, as the original NIfTI loader did.

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


def batch_accumulation_step(batch_size: int) -> int:
    """Accumulate gradients up to an effective batch of 8 when the configured batch is smaller."""
    if batch_size < 8:
        return 8 // batch_size
    return 1


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
        torch.hub.set_dir(f"{working_dir}/torch_hub")
        self.losses_ae = {
            "reconstruction_loss": torch.nn.L1Loss(),
            "kld_loss": KLDivergenceLoss(),
            "gan_loss": PatchAdversarialLoss(criterion="least_squares"),
            "perceptual_loss": PerceptualLoss(2, network_type="alex"),
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
            "optimizer_g": torch.optim.Adam(
                self.model.autoencoder.parameters(), lr=self.params_autoencoder["lr_g"], weight_decay=1e-6, amsgrad=True
            ),
            "optimizer_d": torch.optim.Adam(
                self.model.discriminator.parameters(),
                lr=self.params_autoencoder["lr_d"],
                weight_decay=1e-6,
                amsgrad=True,
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
        self.site_name = flare.get_site_name()
        site_name = self.site_name.replace("-", "")
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

    def reset_perceptual_to_anisotropic(self):
        """Swap the perceptual loss for its 2-D form when 3-D data turns out to be thick-slice.

        3-D only, and not called by the shipped 2-D pipeline: a 3-D port's loader should call this once
        :func:`detect_axial_anisotropy` returns True (setting ``self.axial_anisotropy`` first). The
        weight is raised alongside the swap because this network reports much smaller values.
        """
        if self.axial_anisotropy and self.losses_ae["perceptual_loss"].spatial_dims == 3:
            self.losses_ae["perceptual_loss"] = PerceptualLoss(spatial_dims=2, network_type="radimagenet_resnet50")
            if self.weights_ae["w_perceptual_loss"] <= 1.0:
                self.weights_ae["w_perceptual_loss"] = 10
                # This perceptual loss tends to have very low values.
            logger.info("Resetting perceptual loss to 2D versions due to axial anisotropy.")

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
                with autocast(enabled=True, device_type=self.device.type):
                    reconstruction, z_mu, z_sigma = self.model.autoencoder(images)
                    if True in torch.isnan(reconstruction):
                        logger.error("Found NaN in the autoencoder reconstruction; stopping training on site.")
                        raise RuntimeError("NaN in autoencoder reconstruction during train")
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

                    # Loss generator
                    train_g_loss_ = l1_loss + kl_loss + p_loss + gan_loss

                # Scale and backprop
                scaler_g.scale(train_g_loss_).backward()

                batch_acc_counter += 1
                if batch_acc_counter % accumulation_step == 0 or ind == (len(train_loader) - 1):
                    scaler_g.step(self.optimizers_ae["optimizer_g"])
                    scaler_g.update()
                    self.optimizers_ae["optimizer_g"].zero_grad(set_to_none=True)

                del z_mu, z_sigma, logits_fake

                # TRAIN DISCRIMINATOR
                self.optimizers_ae["optimizer_d"].zero_grad(set_to_none=True)
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
                scaler_d.scale(d_loss).backward()

                if batch_acc_counter % accumulation_step == 0 or ind == (len(train_loader) - 1):
                    scaler_d.step(self.optimizers_ae["optimizer_d"])
                    scaler_d.update()

                del logits_real, logits_fake, reconstruction

                # Aggregate
                train_g_loss += train_g_loss_.item()
                train_g_ganloss += gan_loss.item()
                train_g_percloss += p_loss.item()
                train_g_l1loss += l1_loss.item()
                train_g_klloss += kl_loss.item()
                train_d_loss += d_loss.item()

                del train_g_loss_, gan_loss, p_loss, l1_loss, kl_loss, d_loss, images

                # Force CUDA cache clearing to prevent memory fragmentation
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    torch.cuda.synchronize()

            # Aggregate at the end
            train_g_loss /= max(1, len(train_loader))
            train_g_ganloss /= max(1, len(train_loader))
            train_g_percloss /= max(1, len(train_loader))
            train_g_l1loss /= max(1, len(train_loader))
            train_g_klloss /= max(1, len(train_loader))
            train_d_loss /= max(1, len(train_loader))

            # Validation
            val_loss = 0
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
                        # Always the FIRST validation batch, so the debug images show the same
                        # radiographs every epoch and can be watched sharpening (or not) over rounds.
                        debug_batch = {"input": images, "reconstruction": reconstruction}
                    val_loss += (
                        self.weights_ae["w_reconstruction_loss"]
                        * self.losses_ae["reconstruction_loss"](reconstruction.float(), images.float()).item()
                    )

            val_loss /= max(1, len(val_loader))
            self.model.autoencoder.train()

            logger.info(
                f"Epoch {epoch + 1} / {epochs};\n "
                f"Total loss G: {train_g_loss}, GAN: {train_g_ganloss} "
                f"Perceptual: {train_g_percloss}, L1: {train_g_l1loss} "
                f"KLD: {train_g_klloss} \n"
                f"Total loss D: {train_d_loss},Validation loss (L1): {val_loss}"
            )

            # Send metrics to FLIP. Labels match the legacy tutorial's series names; the "@epoch"
            # suffix names the x-axis and `step` (cumulative local epoch) is the coordinate.
            step = global_round * epochs + epoch + 1
            writer.add_scalar("Train loss (G)@epoch", train_g_loss, global_step=step)
            writer.add_scalar("Train loss (D)@epoch", train_d_loss, global_step=step)
            writer.add_scalar("Perceptual loss (G)@epoch", train_g_percloss, global_step=step)
            writer.add_scalar("KLD loss (G)@epoch", train_g_klloss, global_step=step)
            writer.add_scalar("Reconstruction loss (G)@epoch", train_g_l1loss, global_step=step)
            writer.add_scalar("GAN loss (G)@epoch", train_g_ganloss, global_step=step)
            writer.add_scalar("Validation loss (L1)@epoch", val_loss, global_step=step)

            # Client-local debug images; a no-op unless config.json sets SAVE_DEBUG_SAMPLES.
            # Reconstructions are the one thing that says whether this is optimising: the L1 curve
            # falls just as convincingly while the autoencoder learns to emit a plausible blur.
            save_grid(debug_batch, "reconstruction", self.config, site_name=self.site_name, step=step)

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
        tuple[float, float]: ``(l1_test_loss, ssim_test)`` averaged over the held-out split.
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
        reconstruction = reconstruction.detach().cpu()
        images = images.detach().cpu()
        reconstruction_norm = (reconstruction - reconstruction.min()) / (reconstruction.max() - reconstruction.min())

        _, ssim_metric = compute_ssim_and_cs(
            reconstruction_norm,
            images.detach().cpu(),
            spatial_dims=len(reconstruction.shape[2:]),
            data_range=1,
            kernel_size=[11] * len(reconstruction.shape[2:]),
            kernel_sigma=[1.5] * len(reconstruction.shape[2:]),
        )
        l1_loss = reconstruction_loss(reconstruction.float(), images.float())
        ssim_test += ssim_metric.mean().item()
        l1_test_loss += l1_loss.item()

    l1_test_loss /= max(1, len(test_loader))
    ssim_test /= max(1, len(test_loader))

    logger.info(f"Validation loss: {l1_test_loss} SSIM: {ssim_test}")

    writer.add_scalar("val_l1_loss", l1_test_loss, global_step=0)
    writer.add_scalar("val_ssim", ssim_test, global_step=0)

    # How the AGGREGATED autoencoder reconstructs this site's data — the interesting comparison
    # against the per-epoch grids above, which are of the purely local model.
    save_grid(debug_batch, "reconstruction_aggregated", config, site_name=flare.get_site_name())

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
