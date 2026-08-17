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

"""NVFLARE Client API script for the FLIP latent diffusion tutorial.

One script serves all four ML task names of the two-phase latent-diffusion job — the two training
phases (``train_ae``: autoencoder + GAN discriminator; ``train_dm``: diffusion model over the frozen
autoencoder's latent space) and their cross-site validation passes (``validate_ae`` /
``validate_dm``). The single-name ``flare.is_train()`` / ``flare.is_evaluate()`` predicates cannot
distinguish the two train phases, so dispatch is on ``get_task_name()``.

Training tasks send back a full-model weight **diff** (``params_type="DIFF"``; the diffs reach the
stock ``WEIGHT_DIFF`` aggregator untouched, which averages them directly) stamped with the
``FlipMetaKey.STAGE`` meta that scopes the ``StagePercentilePrivacy`` DP filter to the modules the
phase actually trains. Validation tasks send back aggregate metrics only.

Ported from the legacy Executor-based ``FLIP_TRAINER`` / ``FLIP_VALIDATOR`` pair — the training and
validation maths are unchanged; only the NVFLARE plumbing moved to the Client API.
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
from flip import FLIP
from flip.constants import FlipMetaKey, ResourceType
from models import get_model
from monai.data import DataLoader, Dataset
from monai.losses import PatchAdversarialLoss, PerceptualLoss
from monai.networks.schedulers import DDPMScheduler
from nvflare.client.api import get_task_name
from nvflare.client.tracking import SummaryWriter
from torch.amp import GradScaler, autocast
from transforms import get_train_transforms, get_val_transforms
from validator import build_inferer, validate_ae, validate_dm

logger = logging.getLogger(__name__)

TRAIN_AE_TASK = "train_ae"
TRAIN_DM_TASK = "train_dm"
VALIDATE_AE_TASK = "validate_ae"
VALIDATE_DM_TASK = "validate_dm"

# Module prefixes trained by each phase — stamped as FlipMetaKey.STAGE on the outgoing update so
# the StagePercentilePrivacy filter computes its percentile cutoff over exactly the trained params.
STAGE_MODULES = {
    TRAIN_AE_TASK: [["autoencoder", "discriminator"]],
    TRAIN_DM_TASK: [["diffusion_model"]],
}


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


class DiffusionTrainer:
    """Holds the model, losses, optimizers, and data for both training phases.

    One instance lives for the whole ``flare.is_running()`` loop, so — as with the legacy per-phase
    executor instances — each phase's optimizer state persists across that phase's global rounds.
    """

    def __init__(self, config: dict, project_id: str, query: str):
        self.config = config
        self.project_id = project_id
        working_dir = Path(__file__).parent.resolve()

        #  Training parameters for the autoencoder and the diffusion model
        self.params_autoencoder = {
            "lr_g": config["LR_G"],
            "lr_d": config["LR_D"],
            "epochs": config.get("LOCAL_ROUNDS_AE", 5),
        }
        self.params_diffusion = {"lr": config["LR_DM"], "epochs": config.get("LOCAL_ROUNDS_DM", 5)}

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
        self.perceptual_slices = 12
        self.losses_dm = {"loss": torch.nn.functional.mse_loss}
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
        self.axial_anisotropy: bool | None = None
        dataframe = self.flip.get_dataframe(project_id=project_id, query=query)
        self.train_items, self.val_items = self.get_image_list(dataframe)

        # Per-site split for local/simulator runs where every client reads the same DEV dataset;
        # in production each trust's data-access API already scopes the cohort to its own data.
        site_name = flare.get_site_name()
        if site_name == "site1":
            self.train_items = self.train_items[: len(self.train_items) // 2]
        elif site_name == "site2":
            self.train_items = self.train_items[len(self.train_items) // 2 :]

        self._train_dataset = Dataset(self.train_items, transform=get_train_transforms(config["spatial_shape"]))
        self._val_dataset = Dataset(self.val_items, transform=get_val_transforms(config["spatial_shape"]))

    def get_image_list(self, dataframe) -> tuple[list, list]:
        """Fetch each accession's converted NIfTI images and split into train/validation lists.

        Also detects axial anisotropy (thick-slice data) from the first readable image, which
        switches the perceptual loss to its 2D sliced form.
        """
        datalist: list[dict[str, str]] = []

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

            all_images = list(accession_folder_path.rglob("input_*.nii.gz"))
            if not all_images:
                continue
            if self.axial_anisotropy is None:
                try:
                    nib_image = np.asarray(nib.load(str(all_images[0])).dataobj)
                    # ! We assume that it's axial anisotropy: aka, thick slice.
                    ip_2_op = nib_image.shape[0] / nib_image.shape[-1]
                    if ip_2_op > 1.5:
                        self.axial_anisotropy = True
                        logger.info(f"Found axial anisotropy (in-plane to out-plane ratio is {ip_2_op}).")
                        self.reset_perceptual_to_anisotropic()
                        if self.config["net_config"]["discriminator"]["spatial_dims"] == 3:
                            logger.warning(
                                "Warning: your data is anisotropic on the axial plane "
                                "but the discriminator is still 3D. This might lead to low performance."
                            )
                    else:
                        self.axial_anisotropy = False
                except Exception as e:
                    logger.info(f"Error loading NIfTI image for {accession_id}: {e}")

            for image in all_images:
                datalist.append({"image": str(image)})

        logger.info(f"Found {len(datalist)} files in total.")

        # Validation / train splits:
        val_size = int(self.config["VAL_SPLIT"] * len(datalist))
        return datalist[val_size:], datalist[:val_size]

    def reset_perceptual_to_anisotropic(self):
        """If we spot axial anisotropy, we reset the perceptual loss to 2D if it was 3D to have better results."""
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

    def train_ae(self, writer: SummaryWriter, global_round: int) -> int:
        """One ``train_ae`` round: local AE + discriminator epochs. Returns the iteration count."""
        accumulation_step = batch_accumulation_step(self.config["BATCH_SIZE_AE"])
        train_loader, val_loader = self.make_loaders(self.config["BATCH_SIZE_AE"])
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
                perceptual_slices = None  # Just in case!

                # TRAIN GENERATOR
                with autocast(enabled=True, device_type=self.device.type):
                    reconstruction, z_mu, z_sigma = self.model.autoencoder(images)
                    if True in torch.isnan(reconstruction):
                        logger.error("Found NaN in the autoencoder reconstruction; stopping training on site.")
                        raise RuntimeError("NaN in autoencoder reconstruction during train_ae")
                    kl_loss = self.weights_ae["w_kl_loss"] * self.losses_ae["kld_loss"](z_mu, z_sigma)
                    l1_loss = self.weights_ae["w_reconstruction_loss"] * self.losses_ae["reconstruction_loss"](
                        reconstruction.float(), images.float()
                    )
                    if self.axial_anisotropy or self.config["net_config"]["stage_1"]["spatial_dims"] == 2:
                        perceptual_slices = np.random.randint(0, images.shape[-1], size=self.perceptual_slices)
                        p_loss = self.losses_ae["perceptual_loss"](
                            einops.rearrange(
                                reconstruction[..., perceptual_slices], "b c h w d -> (b d) c h w"
                            ).float(),
                            einops.rearrange(images[..., perceptual_slices], "b c h w d -> (b d) c h w").float(),
                        )
                        p_loss = self.weights_ae["w_perceptual_loss"] * p_loss
                    else:
                        p_loss = self.losses_ae["perceptual_loss"](reconstruction.float(), images.float())
                        p_loss = self.weights_ae["w_perceptual_loss"] * p_loss

                    if (
                        self.config["net_config"]["stage_1"]["spatial_dims"] == 3
                        and self.config["net_config"]["discriminator"]["spatial_dims"] == 2
                    ):
                        if perceptual_slices is None:
                            perceptual_slices = np.random.randint(0, images.shape[-1], size=self.perceptual_slices)
                        logits_fake = self.model.discriminator(
                            einops.rearrange(reconstruction[..., perceptual_slices], "b c h w d -> (b d) c h w")
                            .contiguous()
                            .float()
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
                if (
                    self.config["net_config"]["stage_1"]["spatial_dims"] == 3
                    and self.config["net_config"]["discriminator"]["spatial_dims"] == 2
                ):
                    if perceptual_slices is None:
                        perceptual_slices = np.random.randint(0, images.shape[-1], size=self.perceptual_slices)

                    logits_fake = self.model.discriminator(
                        einops.rearrange(reconstruction.detach()[..., perceptual_slices], "b c h w d -> (b d) c h w")
                        .contiguous()
                        .float()
                    )[-1]
                    logits_real = self.model.discriminator(
                        einops.rearrange(images[..., perceptual_slices], "b c h w d -> (b d) c h w")
                        .contiguous()
                        .float()
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
            self.model.autoencoder.eval()
            for batch in val_loader:
                images = batch["image"].to(self.device)
                with autocast(enabled=True, device_type=self.device.type):
                    with torch.no_grad():
                        reconstruction, _, _ = self.model.autoencoder(images)
                    if True in torch.isnan(reconstruction):
                        break
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

        return epochs * len(train_loader)

    def train_dm(self, writer: SummaryWriter, global_round: int) -> int:
        """One ``train_dm`` round: local diffusion-model epochs. Returns the iteration count."""
        accumulation_step = batch_accumulation_step(self.config["BATCH_SIZE_DM"])
        train_loader, val_loader = self.make_loaders(self.config["BATCH_SIZE_DM"])
        self.model.diffusion_model.to(device=self.device)
        self.model.autoencoder.to(device=self.device)

        inferer, ldm_latent_shape = build_inferer(
            self.model,
            self.optimizers_dm["scheduler"],
            self.config["spatial_shape"],
            next(iter(train_loader))["image"],
            self.device,
        )
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
                    noise = torch.randn(
                        [images.shape[0]]
                        + [self.model.autoencoder.encoder.blocks[-1].out_channels]
                        + ldm_latent_shape
                    ).to(self.device)
                    timesteps = torch.randint(
                        0,
                        self.optimizers_dm["scheduler"].num_train_timesteps,
                        (images.shape[0],),
                        device=self.device,
                    ).long()
                    noise_pred = inferer(
                        inputs=images,
                        diffusion_model=self.model.diffusion_model,
                        autoencoder_model=self.model.autoencoder,
                        noise=noise,
                        timesteps=timesteps,
                        condition=None,
                        mode="crossattn",
                    )
                    loss = self.losses_dm["loss"](noise.float(), noise_pred.float())

                if True in torch.isnan(loss) or True in torch.isnan(noise_pred):
                    logger.error("Found NaN on training loss; stopping training on site.")
                    raise RuntimeError("NaN loss during train_dm")

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
                    noise = torch.randn(
                        [images.shape[0]]
                        + [self.model.autoencoder.encoder.blocks[-1].out_channels]
                        + ldm_latent_shape
                    ).to(self.device)
                    timesteps = torch.randint(
                        0,
                        self.optimizers_dm["scheduler"].num_train_timesteps,
                        (images.shape[0],),
                        device=self.device,
                    ).long()
                    noise_pred = inferer(
                        inputs=images,
                        diffusion_model=self.model.diffusion_model,
                        autoencoder_model=self.model.autoencoder,
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


def to_torch_weights(input_model: flare.FLModel) -> dict[str, torch.Tensor]:
    return {k: torch.as_tensor(v) for k, v in input_model.params.items()}


def send_weight_diff(original_params: dict, model: torch.nn.Module, n_iterations: int, task_name: str) -> None:
    """Send the full-model weight diff for a completed training round.

    Built one tensor at a time to avoid holding a second full copy of the model in RAM (mirrors
    ``flip.utils.get_model_weights_diff``). The ``STAGE`` meta scopes the DP filter's percentile
    cutoff to the modules this phase trained.
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
            meta={
                "NUM_STEPS_CURRENT_ROUND": n_iterations,
                FlipMetaKey.STAGE.value: STAGE_MODULES[task_name],
            },
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

        task_name = get_task_name()
        logger.info(f"[LDM trainer] received task '{task_name}' (round {input_model.current_round})")
        weights = to_torch_weights(input_model)
        global_round = input_model.current_round or 0

        if task_name == TRAIN_AE_TASK:
            trainer.load_weights(weights)
            n_iterations = trainer.train_ae(writer, global_round)
            send_weight_diff(input_model.params, trainer.model, n_iterations, task_name)

        elif task_name == TRAIN_DM_TASK:
            trainer.load_weights(weights)
            n_iterations = trainer.train_dm(writer, global_round)
            send_weight_diff(input_model.params, trainer.model, n_iterations, task_name)

        elif task_name == VALIDATE_AE_TASK:
            trainer.model.load_state_dict({k: v.to(trainer.device) for k, v in weights.items()})
            test_loader = trainer.val_loader(config["BATCH_SIZE_AE"])
            val_loss, val_ssim = validate_ae(trainer.model, test_loader, trainer.device, writer)
            logger.info(f"Validating the aggregated autoencoder on {flare.get_site_name()}'s data: SSIM {val_ssim}")
            flare.send(flare.FLModel(metrics={f"metrics_{task_name}": {"val_loss": val_loss, "val_ssim": val_ssim}}))

        elif task_name == VALIDATE_DM_TASK:
            trainer.model.load_state_dict(
                {k: v.to(trainer.device) for k, v in weights.items()}, strict=False
            )
            test_loader = trainer.val_loader(config["BATCH_SIZE_DM"])
            val_loss = validate_dm(
                trainer.model, test_loader, trainer.optimizers_dm["scheduler"], config, trainer.device, writer
            )
            logger.info(f"Validating the aggregated diffusion model on {flare.get_site_name()}'s data: {val_loss}")
            flare.send(flare.FLModel(metrics={f"metrics_{task_name}": {"val_loss": val_loss}}))

        else:
            logger.warning(f"Received unknown task '{task_name}'; ignoring.")


if __name__ == "__main__":
    main()
