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

import json
import os.path
from pathlib import Path

import einops
import nibabel as nib
import numpy as np
import torch
from models import get_model
from monai.data import DataLoader, Dataset
from monai.inferers import LatentDiffusionInferer
from monai.losses import PatchAdversarialLoss, PerceptualLoss
from monai.networks.schedulers import DDPMScheduler
from nvflare.apis.dxo import DataKind, from_shareable
from nvflare.apis.executor import Executor
from nvflare.apis.fl_constant import FLContextKey, ReservedKey, ReturnCode
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable, make_reply
from nvflare.apis.signal import Signal
from nvflare.app_common.abstract.model import make_model_learnable, model_learnable_to_dxo
from nvflare.app_common.app_constant import AppConstants
from nvflare.app_common.pt.pt_fed_utils import PTModelPersistenceFormatManager
from torch.amp import GradScaler, autocast
from transforms import get_train_transforms, get_val_transforms

from flip import FLIP
from flip.constants import FlipConstants, PTConstants, ResourceType
from flip.utils import get_model_weights_diff


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


class FLIP_TRAINER(Executor):
    def __init__(
        self,
        epochs_ae=5,
        epochs_dm=5,
        train_task_name=AppConstants.TASK_TRAIN,
        submit_model_task_name=AppConstants.TASK_SUBMIT_MODEL,
        exclude_vars=None,
        project_id="",
        query="",
    ):
        """ """

        super(FLIP_TRAINER, self).__init__()

        self._train_task_name = train_task_name
        self._submit_model_task_name = submit_model_task_name
        self._exclude_vars = exclude_vars
        self.config = {}
        working_dir = Path(__file__).parent.resolve()
        self.working_dir = working_dir
        self.flip = FLIP()
        self.project_id = project_id
        self.query = query

        # Load config parameters
        with open(str(working_dir / "config.json")) as file:
            self.config = json.load(file)

        if "LOCAL_ROUNDS_AE" in self.config.keys():
            epochs_ae_ = self.config["LOCAL_ROUNDS_AE"]
        else:
            epochs_ae_ = epochs_ae
        if "LOCAL_ROUNDS_DM" in self.config.keys():
            epochs_dm_ = self.config["LOCAL_ROUNDS_DM"]
        else:
            epochs_dm_ = epochs_dm

        #  Training parameters for the autoencoder and the diffusion model
        self.params_autoencoder = {"lr_g": self.config["LR_G"], "lr_d": self.config["LR_D"], "epochs": epochs_ae_}
        self.trained_autoencoder = False
        self.params_diffusion = {"lr": self.config["LR_DM"], "epochs": epochs_dm_}
        self.trained_discriminator = False

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
            "w_reconstruction_loss": self.config["w_reconstruction_loss"],
            "w_perceptual_loss": self.config["w_perceptual_loss"],
            "w_kl_loss": self.config["w_kl_loss"],
            "w_gan_loss": self.config["w_gan_loss"],
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
        self.axial_anisotropy = None
        self.dataframe = self.flip.get_dataframe(project_id=self.project_id, query=self.query)
        self.train_dict, self.val_dict = self.get_image_and_label_list(self.dataframe)

        # Get transforms
        self._transforms = get_train_transforms(self.config["spatial_shape"])
        self._val_transforms = get_val_transforms(self.config["spatial_shape"])
        self._default_train_conf = {"train": {"model": type(self.model).__name__}}
        self.persistence_manager = PTModelPersistenceFormatManager(
            data=self.model.state_dict(), default_train_conf=self._default_train_conf
        )

        self.plot_images_every_local = 5

    def get_num_epochs(self):
        """Returns the maximum number of epochs for either training phase."""
        return max(self.params_autoencoder["epochs"], self.params_diffusion["epochs"])

    def config_batch_accumulation(self, phase: str):
        # Set batch accumulation
        if self.config[phase] < 8:
            self.batch_accumulation_step = 8 // self.config[phase]
        else:
            self.batch_accumulation_step = 1

    def get_image_and_label_list(self, dataframe, get_val: bool = False):
        """Returns a list of dicts, each dict containing the path to an image and its corresponding label."""

        datalist = []

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
                print(f"Could not get image data folder path for {accession_id}: {err}")
                continue

            all_images = list(accession_folder_path.rglob("input_*.nii.gz"))
            if self.axial_anisotropy is None:
                try:
                    nib_image = np.asarray(nib.load(str(all_images[0])).dataobj)
                    # ! We assume that it's axial anisotropy: aka, thick slice.
                    ip_2_op = nib_image.shape[0] / nib_image.shape[-1]
                    if ip_2_op > 1.5:
                        self.axial_anisotropy = True
                        print(f"Found axial anisotropy (in-plane to out-plane ratio is {ip_2_op}).")
                        self.reset_perceptual_to_anisotropic()
                        if self.config["net_config"]["discriminator"]["spatial_dims"] == 3:
                            print(
                                "Warning: your data is anisotropic on the axial plane"
                                "but the discriminator is still 3D. This might lead to low performance."
                            )
                    else:
                        self.axial_anisotropy = False

                except Exception as e:
                    print(f"Error loading NIfTI image for {accession_id}: {e}")

            datalist.append({"image": str(i) for i in all_images})

        print(f"Found {len(datalist)} files in total.")

        # Validation / train splits:
        return datalist[int(self.config["VAL_SPLIT"] * len(datalist)) :], datalist[
            : int(self.config["VAL_SPLIT"] * len(datalist))
        ]

    def reset_perceptual_to_anisotropic(self):
        """If we spot axial anisotropy, we reset the perceptual loss to 2D if it was 3D to have better results."""
        if self.axial_anisotropy and self.losses_ae["perceptual_loss"].spatial_dims == 3:
            self.losses_ae["perceptual_loss"] = PerceptualLoss(spatial_dims=2, network_type="radimagenet_resnet50")
            if self.weights_ae["w_perceptual_loss"] <= 1.0:
                self.weights_ae["w_perceptual_loss"] = 10
                # This perceptual loss tends to have very low values.
            print("Resetting perceptual loss to 2D versions due to axial anisotropy.")

    def derive_new_latent_shape(self, input_shape: list, num_downsamplings: int) -> list:
        """
        For diffusion model, adjusts the stage 1 latent space so that the latent space input to the
        diffusion model can be downsampled as many times as needed without errors due to odd dimensions.
        """
        output_shape = []
        for shape_el in input_shape:
            new_shape = shape_el
            remainder_0 = shape_el % 2**num_downsamplings
            while remainder_0 != 0:
                new_shape += 1
                remainder_0 = new_shape % 2**num_downsamplings

            output_shape.append(new_shape)
        return [int(i) for i in output_shape]

    def local_train_ae(self, fl_ctx, weights, abort_signal, global_round: int = 0):
        self.config_batch_accumulation(phase="BATCH_SIZE_AE")
        # This implies loading the autoencoder and discriminator weights.
        self.model.load_state_dict(state_dict=weights, strict=False)
        self.model.autoencoder.to(device=self.device)
        self.model.discriminator.to(device=self.device)
        self.losses_ae["perceptual_loss"].to(self.device)

        # Create GradScalers - ensure they don't accumulate state
        scaler_g = GradScaler(enabled=True)
        scaler_d = GradScaler(enabled=True)

        # Plot dir
        if FlipConstants.LOCAL_DEV:
            os.makedirs(os.path.join(os.path.join(os.getcwd(), fl_ctx.get_job_id()), "saved_images_ae"), exist_ok=True)

        # Basic training
        self.model.autoencoder.train()
        self.model.discriminator.train()
        val_loss_total = []
        nan_signal = False
        for epoch in range(self.params_autoencoder["epochs"]):
            if nan_signal:
                abort_signal.trigger()
                self.log_info(
                    fl_ctx,
                    "Stopping training on site due to NaN loss values.",
                )
                break

            train_g_loss = 0
            train_d_loss = 0
            train_g_l1loss = 0
            train_g_percloss = 0
            train_g_ganloss = 0
            train_g_klloss = 0

            batch_acc_counter = 0
            for ind, batch in enumerate(self._train_loader):
                if abort_signal.triggered:
                    return

                images = batch["image"].to(self.device)
                perceptual_slices = None  # Just in case!

                # TRAIN GENERATOR
                with autocast(enabled=True, device_type=self.device.type):
                    reconstruction, z_mu, z_sigma = self.model.autoencoder(images)
                    if True in torch.isnan(reconstruction):
                        abort_signal.trigger()
                        nan_signal = True
                        break
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

                if nan_signal:
                    break

                # Scale and backprop
                scaler_g.scale(train_g_loss_).backward()
                # scaler_g.unscale_(self.optimizers_ae["optimizer_g"])

                batch_acc_counter += 1
                if batch_acc_counter % self.batch_accumulation_step == 0 or ind == (len(self._train_loader) - 1):
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
                # scaler_d.unscale_(self.optimizers_ae["optimizer_d"])

                if batch_acc_counter % self.batch_accumulation_step == 0 or ind == (len(self._train_loader) - 1):
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
            train_g_loss /= max(1, len(self._train_loader))
            train_g_ganloss /= max(1, len(self._train_loader))
            train_g_percloss /= max(1, len(self._train_loader))
            train_g_l1loss /= max(1, len(self._train_loader))
            train_g_klloss /= max(1, len(self._train_loader))
            train_d_loss /= max(1, len(self._train_loader))

            # Validation
            val_loss = 0
            self.model.autoencoder.eval()
            # plot_index = np.random.randint(0, len(self._val_loader))
            for ind, batch in enumerate(self._val_loader):
                if abort_signal.triggered:
                    # If abort_signal is triggered, we simply return.
                    # The outside function will check it again and decide steps to take.
                    return

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
                # if ind == plot_index and FlipConstants.LOCAL_DEV:
                #     from utils_plot import plot_ae_images

                #     # We plot autoencoder images just to check them
                #     plot_ae_images(
                #         torch.stack([reconstruction.detach().cpu(), images.detach().cpu()], dim=0),
                #         save_path=os.path.join(
                #             os.path.join(os.getcwd(), fl_ctx.get_job_id()), "saved_images_ae", f"AE_TR_{epoch}.png"
                #         ),
                #     )

            val_loss /= max(1, len(self._val_loader))
            val_loss_total.append(val_loss)
            self.model.autoencoder.train()

            # To print
            to_print = [
                f"Epoch {epoch + 1} / {self.params_autoencoder['epochs']};\n ",
                f"Total loss G: {train_g_loss}, GAN: {train_g_ganloss} ",
                f"Perceptual: {train_g_percloss}, L1: {train_g_l1loss} ",
                f"KLD: {train_g_klloss} \n",
                f"Total loss D: {train_d_loss},Validation loss (L1): {val_loss}",
            ]

            to_print = "".join(to_print)
            self.log_info(
                fl_ctx,
                to_print,
            )

            # Send metrics to flip
            self.flip.send_metrics_value(label="Train loss (G)", value=train_g_loss, fl_ctx=fl_ctx)
            self.flip.send_metrics_value(label="Train loss (D)", value=train_d_loss, fl_ctx=fl_ctx)
            self.flip.send_metrics_value(label="Perceptual loss (G)", value=train_g_percloss, fl_ctx=fl_ctx)
            self.flip.send_metrics_value(label="KLD loss (G)", value=train_g_klloss, fl_ctx=fl_ctx)
            self.flip.send_metrics_value(label="Reconstruction loss (G)", value=train_g_l1loss, fl_ctx=fl_ctx)
            self.flip.send_metrics_value(label="GAN loss (G)", value=train_g_ganloss, fl_ctx=fl_ctx)
            self.flip.send_metrics_value(label="Validation loss (L1)", value=val_loss, fl_ctx=fl_ctx)

        return np.mean(val_loss_total)

    def local_train_dm(self, fl_ctx, weights, abort_signal, global_round: int = 0):
        self.config_batch_accumulation(phase="BATCH_SIZE_DM")
        # This implies loading the autoencoder and discriminator weights.
        self.model.load_state_dict(state_dict=weights, strict=False)
        self.model.diffusion_model.to(device=self.device)
        self.model.autoencoder.to(device=self.device)

        if FlipConstants.LOCAL_DEV:
            os.makedirs(os.path.join(os.path.join(os.getcwd(), fl_ctx.get_job_id()), "saved_images_dm"), exist_ok=True)

        # Infer the latent shape of the autoencoder
        autoencoder_latent_shape = [
            i / (2 ** (len(self.model.autoencoder.decoder.channels) - 1)) for i in self.config["spatial_shape"]
        ]

        ldm_latent_shape = self.derive_new_latent_shape(
            autoencoder_latent_shape, len(self.model.diffusion_model.block_out_channels) - 1
        )

        # Infer scale factor
        with torch.no_grad():
            with autocast(enabled=True, device_type=self.device.type):
                sample_z = self.model.autoencoder.encode_stage_2_inputs(
                    next(iter(self._train_loader))["image"].to(self.device)
                )
                scale_factor = 1 / torch.std(sample_z)
                del sample_z

        # Create GradScalers
        inferer = LatentDiffusionInferer(
            scheduler=self.optimizers_dm["scheduler"],
            ldm_latent_shape=ldm_latent_shape,
            autoencoder_latent_shape=autoencoder_latent_shape,
            scale_factor=scale_factor.item(),
        )
        scaler = GradScaler()

        # Basic training
        nan_signal = False
        train_loss = []
        val_loss = []
        for epoch in range(self.params_diffusion["epochs"]):
            self.model.diffusion_model.train()
            if nan_signal:
                abort_signal.trigger()
                self.log_info(
                    fl_ctx,
                    "Stopping training on site due to NaN loss values.",
                )
                break
            batch_acc_counter = 0
            train_loss_epoch = 0

            for ind, batch in enumerate(self._train_loader):
                if abort_signal.triggered:
                    # If abort_signal is triggered, we simply return.
                    # The outside function will check it again and decide steps to take.
                    return

                images = batch["image"].to(self.device)
                if ind == 0:
                    with torch.no_grad():
                        reconstruction, _, _ = self.model.autoencoder(images)
                        reconstruction = reconstruction.detach().cpu()

                # conditioning = self.get_slice_no(images).to(self.device)
                # if ind == conditioning_save_ind:
                #     conditioning_save = conditioning.detach().cpu()
                # TRAIN GENERATOR

                with autocast(enabled=False, device_type=self.device.type):
                    noise = torch.randn(
                        [images.shape[0]] + [self.model.autoencoder.encoder.blocks[-1].out_channels] + ldm_latent_shape
                    ).to(self.device)
                    timesteps = torch.randint(
                        0, self.optimizers_dm["scheduler"].num_train_timesteps, (images.shape[0],), device=self.device
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
                    abort_signal.trigger()
                    nan_signal = True
                    self.log_info(fl_ctx, "Found NaN on training loss.")
                    break

                scaler.scale(loss).backward()
                if batch_acc_counter == self.batch_accumulation_step:
                    scaler.step(self.optimizers_dm["optimizer"])
                    scaler.update()
                    batch_acc_counter = 0
                    self.optimizers_dm["optimizer"].zero_grad(set_to_none=True)
                train_loss_epoch += loss.item()

            train_loss.append(train_loss_epoch / max(1, len(self._train_loader)))
            # Validation  loss
            val_loss_epoch = 0
            for ind, batch in enumerate(self._val_loader):
                if abort_signal.triggered:
                    # If abort_signal is triggered, we simply return.
                    # The outside function will check it again and decide steps to take.
                    return

                images = batch["image"].to(self.device)
                # conditioning = self.get_slice_no(images).to(self.device)
                # if ind == conditioning_save_ind:
                #    conditioning_save = conditioning.detach().cpu()
                self.model.diffusion_model.eval()
                with autocast(enabled=False, device_type=self.device.type):
                    noise = torch.randn(
                        [images.shape[0]] + [self.model.autoencoder.encoder.blocks[-1].out_channels] + ldm_latent_shape
                    ).to(self.device)
                    timesteps = torch.randint(
                        0, self.optimizers_dm["scheduler"].num_train_timesteps, (images.shape[0],), device=self.device
                    ).long()
                    noise_pred = inferer(
                        inputs=images,
                        diffusion_model=self.model.diffusion_model,
                        autoencoder_model=self.model.autoencoder,
                        noise=noise,
                        timesteps=timesteps,
                        condition=None,  # conditioning,
                        mode="crossattn",
                    )
                    val_loss_epoch += self.losses_dm["loss"](noise.float(), noise_pred.float()).item()

            val_loss.append(val_loss_epoch / max(1, len(self._val_loader)))

            # Sample
            if FlipConstants.LOCAL_DEV:
                self.model.diffusion_model.eval()
                # We sample
                noise = torch.randn(
                    [self.config["BATCH_SIZE_DM"]]
                    + [self.model.autoencoder.encoder.blocks[-1].out_channels]
                    + ldm_latent_shape
                ).to(self.device)
                sampled_images, _ = inferer.sample(
                    input_noise=noise,
                    conditioning=None,
                    diffusion_model=self.model.diffusion_model,
                    scheduler=self.optimizers_dm["scheduler"],
                    save_intermediates=True,
                    autoencoder_model=self.model.autoencoder,
                )
                sampled_images = sampled_images.detach().cpu().numpy()
                # from utils_plot import plot_dm_images

                # plot_dm_images(
                #     sampled_images,
                #     save_path=os.path.join(os.getcwd(), fl_ctx.get_job_id(), "saved_images_dm", f"DM_TR_{epoch}.png"),
                # )

            # To printtotal_val_loss
            to_print = [
                f"Epoch {epoch + 1} / {self.params_diffusion['epochs']};\n ",
                f"Total loss DM: {np.mean(train_loss)}",
            ]
            to_print = "".join(to_print)
            self.log_info(
                fl_ctx,
                to_print,
            )

            self.flip.send_metrics_value(label="Total loss DM", value=np.mean(train_loss), fl_ctx=fl_ctx)
            self.flip.send_metrics_value(label="Validation loss DM", value=np.mean(val_loss), fl_ctx=fl_ctx)

        return np.mean(val_loss)

    def execute(
        self,
        task_name: str,
        shareable: Shareable,
        fl_ctx: FLContext,
        abort_signal: Signal,
    ) -> Shareable:
        # Diagnostic logging: confirm this trainer file and what task it's handling
        self.log_info(
            fl_ctx,
            f"[FLIP_TRAINER] Loaded from {__file__}; configured _train_task_name='{self._train_task_name}', "
            f"_submit_model_task_name='{self._submit_model_task_name}'; incoming task_name='{task_name}'",
        )

        site_name = fl_ctx.get_prop(FLContextKey.CLIENT_NAME, "")
        if "site1" == site_name:
            train_dict = self.train_dict[: int(len(self.train_dict) // 2)]
        elif "site2" == site_name:
            train_dict = self.train_dict[int(len(self.train_dict) // 2) :]
        else:
            train_dict = self.train_dict

        self._train_dataset = Dataset(train_dict, transform=self._transforms)
        self._val_dataset = Dataset(self.val_dict, transform=self._val_transforms)

        # Accept either the exact configured task name (e.g. "train_ae"/"train_dm")
        # or a more generic "train*" task name coming from the controller.
        if task_name == self._train_task_name or (
            task_name.startswith("train") and str(self._train_task_name).startswith("train")
        ):
            # Get model weights
            dxo = from_shareable(shareable)

            # Ensure data kind is weights.
            if not dxo.data_kind == DataKind.WEIGHTS:
                self.log_error(
                    fl_ctx,
                    f"data_kind expected WEIGHTS but got {dxo.data_kind} instead.",
                )
                return make_reply(ReturnCode.BAD_TASK_DATA)

            # Convert weights to tensor. Run training
            torch_weights = {k: torch.as_tensor(v) for k, v in dxo.data.items()}
            self.log_info(fl_ctx, "Set to start the local training.")
            global_round = shareable.get_header(AppConstants.CURRENT_ROUND)

            if self._train_task_name == "train_ae":
                self._train_loader = DataLoader(
                    self._train_dataset, batch_size=self.config["BATCH_SIZE_AE"], shuffle=True, num_workers=1
                )
                self._val_loader = DataLoader(
                    self._val_dataset, batch_size=self.config["BATCH_SIZE_AE"], shuffle=True, num_workers=1
                )
                self._n_iterations = len(self._train_loader)
                _ = self.local_train_ae(fl_ctx, torch_weights, abort_signal, global_round=global_round)
            elif self._train_task_name == "train_dm":
                self._train_loader = DataLoader(
                    self._train_dataset, batch_size=self.config["BATCH_SIZE_DM"], shuffle=True, num_workers=1
                )
                self._val_loader = DataLoader(
                    self._val_dataset, batch_size=self.config["BATCH_SIZE_DM"], shuffle=True, num_workers=1
                )
                self._n_iterations = len(self._train_loader)
                _ = self.local_train_dm(fl_ctx, torch_weights, abort_signal, global_round=global_round)

            # Check the abort_signal after training.
            # local_train returns early if abort_signal is triggered.
            if abort_signal.triggered:
                return make_reply(ReturnCode.TASK_ABORTED)

            # Save the local model after training.
            self.save_local_model(fl_ctx)

            # Get the new state dict and send as weights
            new_weights = self.model.state_dict()
            outgoing_dxo = get_model_weights_diff(dxo.data, new_weights, self._n_iterations)

            # outgoing_dxo_metrics = DXO(
            #     data_kind=DataKind.METRICS,
            #     data={'val_loss': total_val_loss},
            #     meta={MetaKey.NUM_STEPS_CURRENT_ROUND: self._n_iterations}
            # )

            # outgoing_dxo = DXO(
            #     data = {"weights": outgoing_dxo_model, "metrics": outgoing_dxo_metrics},
            #     data_kind=DataKind.COLLECTION,
            #     meta={MetaKey.NUM_STEPS_CURRENT_ROUND: self._n_iterations})

            return outgoing_dxo.to_shareable()

        elif task_name == self._submit_model_task_name:
            # Load local model
            ml = self.load_local_model(fl_ctx)

            # Get the model parameters and create dxo from it
            dxo = model_learnable_to_dxo(ml)
            return dxo.to_shareable()
        else:
            return make_reply(ReturnCode.TASK_UNKNOWN)

    def save_local_model(self, fl_ctx: FLContext):
        run_dir = fl_ctx.get_engine().get_workspace().get_run_dir(fl_ctx.get_prop(ReservedKey.RUN_NUM))
        models_dir = os.path.join(run_dir, PTConstants.PTModelsDir)
        if not os.path.exists(models_dir):
            os.makedirs(models_dir)
        model_path = os.path.join(models_dir, PTConstants.PTLocalModelName)

        ml = make_model_learnable(self.model.state_dict(), {})
        self.persistence_manager.update(ml)
        torch.save(self.persistence_manager.to_persistence_dict(), model_path)

    def load_local_model(self, fl_ctx: FLContext):
        run_dir = fl_ctx.get_engine().get_workspace().get_run_dir(fl_ctx.get_prop(ReservedKey.RUN_NUM))
        models_dir = os.path.join(run_dir, PTConstants.PTModelsDir)
        if not os.path.exists(models_dir):
            return None
        model_path = os.path.join(models_dir, PTConstants.PTLocalModelName)

        self.persistence_manager = PTModelPersistenceFormatManager(
            data=torch.load(model_path), default_train_conf=self._default_train_conf
        )
        ml = self.persistence_manager.to_model_learnable(exclude_vars=self._exclude_vars)
        return ml
        return ml
        return ml
        return ml
        return ml
        return ml
