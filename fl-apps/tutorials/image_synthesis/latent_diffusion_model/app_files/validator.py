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
import os
from pathlib import Path

import nibabel as nib
import numpy as np
import torch
from models import get_model
from monai.data import DataLoader, Dataset
from monai.inferers import LatentDiffusionInferer
from monai.metrics import compute_ssim_and_cs
from monai.networks.schedulers import DDPMScheduler
from nvflare.apis.dxo import DXO, DataKind, from_shareable
from nvflare.apis.executor import Executor
from nvflare.apis.fl_constant import ReturnCode
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable, make_reply
from nvflare.apis.signal import Signal
from nvflare.app_common.app_constant import AppConstants
from torch.amp import autocast
from transforms import get_val_transforms

from flip import FLIP
from flip.constants import FlipConstants, ResourceType


class FLIP_VALIDATOR(Executor):
    def __init__(
        self,
        validate_task_name=AppConstants.TASK_VALIDATION,
        project_id="",
        query="",
        local_training_nvflare: bool = False,
    ):
        super(FLIP_VALIDATOR, self).__init__()

        # FLIP-specific: do not modify these variables
        self._validate_task_name = validate_task_name
        self.config = {}
        working_dir = Path(__file__).parent.resolve()
        self.working_dir = working_dir
        self.project_id = project_id
        self.query = query

        # Load config file
        with open(str(working_dir / "config.json")) as file:
            self.config = json.load(file)

        # Model creation
        self.model = get_model()
        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        self.model.to(self.device)

        # Losses, metrics
        self.reconstruction_loss = torch.nn.L1Loss()
        self.w_reconstruction_loss = self.config["w_reconstruction_loss"]

        # Losses, optimizers etc.
        torch.hub.set_dir("/code/torch_hub")
        self.losses_ae = {"reconstruction_loss": torch.nn.L1Loss()}
        self.losses_dm = {"loss": torch.nn.functional.mse_loss}
        self.optimizers_dm = {
            "scheduler": DDPMScheduler(
                num_train_timesteps=1000,
                schedule="scaled_linear_beta",
                clip_sample=False,
                prediction_type="epsilon",
                beta_start=0.0015,
                beta_end=0.0195,
            ),
        }

        # Get transforms
        self._transforms = get_val_transforms(self.config["spatial_shape"])

        # Data loading
        self.flip = FLIP()
        self.axial_anisotropy = None
        self.dataframe = self.flip.get_dataframe(project_id=self.project_id, query=self.query)
        _, self.val_dict = self.get_image_and_label_list(self.dataframe)
        self._test_dataset = Dataset(self.val_dict, transform=self._transforms)

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
                except Exception as e:
                    print(f"Error loading NIfTI image for {accession_id}: {e}")
                # ! We assume that it's axial anisotropy: aka, thick slice.
                ip_2_op = nib_image.shape[0] / nib_image.shape[-1]
                if ip_2_op > 1.5:
                    self.axial_anisotropy = True
                else:
                    self.axial_anisotropy = False

            datalist.append({"image": str(i) for i in all_images})

        print(f"Found {len(datalist)} files in total.")

        # Validation / train splits:
        return datalist[int(self.config["VAL_SPLIT"] * len(datalist)) :], datalist[
            : int(self.config["VAL_SPLIT"] * len(datalist))
        ]

    def get_slice_no(self, image_meta_tensor):
        out_slices = []
        for b in range(image_meta_tensor.shape[0]):
            out_slices.append(
                float(image_meta_tensor.applied_operations[b][1]["extra_info"]["extra_info"]["cropped"][4])
            )
        out_slices = torch.tensor(out_slices)[:, None, None]
        return out_slices

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

    def do_validation_dm(self, fl_ctx, weights, abort_signal):
        # This implies loading the autoencoder and discriminator weights.
        self.model.load_state_dict(state_dict=weights, strict=False)
        self.model.diffusion_model.to(device=self.device)
        self.model.autoencoder.to(device=self.device)

        os.makedirs(
            os.path.join(os.path.join(os.getcwd(), fl_ctx.get_job_id()), "saved_images_dm"),
            exist_ok=True,
        )

        # Infer the latent shape of the autoencoder
        autoencoder_latent_shape = [
            i / (2 ** (len(self.model.autoencoder.decoder.channels) - 1)) for i in self.config["spatial_shape"]
        ]
        ldm_latent_shape = self.derive_new_latent_shape(
            autoencoder_latent_shape,
            len(self.model.diffusion_model.block_out_channels) - 1,
        )

        # Infer scale factor

        with torch.no_grad():
            with autocast(enabled=True, device_type=self.device.type):
                sample_z = self.model.autoencoder.encode_stage_2_inputs(
                    next(iter(self._test_loader))["image"].to(self.device)
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

        # Basic training
        self.model.diffusion_model.eval()
        self.model.autoencoder.eval()

        val_loss = []
        # conditioning_save = np.random.randint(len(self._test_loader))
        for ind, batch in enumerate(self._test_loader):
            if abort_signal.triggered:
                return

            images = batch["image"].to(self.device)
            # conditioning = self.get_slice_no(images).to(self.device)
            # if ind == conditioning_save:
            #    conditioning_save = conditioning.detach().cpu()
            # TRAIN GENERATOR
            with autocast(enabled=False, device_type=self.device.type):
                with torch.no_grad():
                    noise = torch.randn(
                        [images.shape[0]] + [self.model.autoencoder.encoder.blocks[-1].out_channels] + ldm_latent_shape
                    ).to(self.device)
                    timesteps = torch.randint(
                        0,
                        self.optimizers_dm["scheduler"].num_train_timesteps,
                        (images.shape[0],),
                        device=self.device,
                    ).long()
                    self.log_info(
                        fl_ctx,
                        f"Sizes: images =  {images.shape}, noise = {noise.shape}",
                    )
                    noise_pred = inferer(
                        inputs=images,
                        diffusion_model=self.model.diffusion_model,
                        autoencoder_model=self.model.autoencoder,
                        noise=noise,
                        timesteps=timesteps,
                        condition=None,
                        mode="crossattn",
                    )
                loss = self.losses_dm["loss"](noise.float(), noise_pred.float()).item()
                val_loss.append(loss)

        if FlipConstants.LOCAL_DEV:
            self.log_info(fl_ctx, "[DEV]: Sampling images...")
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
            self.log_info(fl_ctx, f"Sampled images shape: {sampled_images.shape}")

            # Plot images
            # from utils_plot import plot_dm_images
            # plot_dm_images(
            #     sampled_images, os.path.join(os.getcwd(), fl_ctx.get_job_id(), "saved_images_dm", "DM_VAL.png")
            # )

        # To print
        to_print = [f"Validation DM: {np.mean(val_loss)}"]
        to_print = "".join(to_print)
        self.log_info(
            fl_ctx,
            to_print,
        )

        self.flip.send_metrics_value(label="Total loss DM (val)", value=np.mean(val_loss), fl_ctx=fl_ctx)

        return np.mean(val_loss)

    def do_validation_ae(self, fl_ctx, weights, abort_signal):
        # Set the model weights
        # This implies loading the autoencoder and discriminator weights.
        self.model.load_state_dict(weights)

        # Basic training
        self.model.eval()

        l1_test_loss = 0
        ssim_test = 0

        for ind, batch in enumerate(self._test_loader):
            if abort_signal.triggered:
                # If abort_signal is triggered, we simply return.
                # The outside function will check it again and decide steps to take.
                return

            images = batch["image"].to(self.device)

            # TRAIN GENERATOR
            with torch.no_grad():
                reconstruction, _, _ = self.model.autoencoder(images)
            reconstruction = reconstruction.detach().cpu()
            images = images.detach().cpu()
            reconstruction_norm = (reconstruction - reconstruction.min()) / (
                reconstruction.max() - reconstruction.min()
            )

            _, ssim_metric = compute_ssim_and_cs(
                reconstruction_norm,
                images.detach().cpu(),
                spatial_dims=len(reconstruction.shape[2:]),
                data_range=1,
                kernel_size=[11] * len(reconstruction.shape[2:]),
                kernel_sigma=[1.5] * len(reconstruction.shape[2:]),
            )
            l1_loss = self.losses_ae["reconstruction_loss"](reconstruction.float(), images.float())
            ssim_test += ssim_metric.mean().item()
            l1_test_loss += l1_loss.item()

            # if FlipConstants.LOCAL_DEV:
            # We save images in the job directory
            # from utils_plot import plot_ae_images  # noqa: E116

            # plot_ae_images(
            #    torch.stack([images, reconstruction], 0).detach().cpu().numpy(),  # noqa: E116
            #     os.path.join(os.getcwd(), fl_ctx.get_job_id(), "saved_images_ae", "AE_VAL.png"),  # noqa: E116
            # )

        # Aggregate at the end
        l1_test_loss /= max(1, len(self._test_loader))
        ssim_test /= max(1, len(self._test_loader))

        # To print
        to_print = [f"Validation loss: {l1_test_loss}", f"SSIM: {ssim_test}"]

        to_print = "".join(to_print)

        self.log_info(
            fl_ctx,
            to_print,
        )

        # Send metrics to flip
        self.flip.send_metrics_value(label="val_l1_loss", value=l1_test_loss, fl_ctx=fl_ctx)
        self.flip.send_metrics_value(label="val_ssim", value=ssim_test, fl_ctx=fl_ctx)

        return l1_test_loss, ssim_test

    def execute(
        self,
        task_name: str,
        shareable: Shareable,
        fl_ctx: FLContext,
        abort_signal: Signal,
    ) -> Shareable:
        if task_name == self._validate_task_name:
            model_owner = "?"
            dxo = from_shareable(shareable)

            # Ensure data_kind is weights.
            if not dxo.data_kind == DataKind.WEIGHTS:
                self.log_exception(
                    fl_ctx,
                    f"DXO is of type {dxo.data_kind} but expected type WEIGHTS.",
                )
                return make_reply(ReturnCode.BAD_TASK_DATA)

            # Extract weights and ensure they are tensor.
            model_owner = shareable.get_header(AppConstants.MODEL_OWNER, "?")
            weights = {k: torch.as_tensor(v, device=self.device) for k, v in dxo.data.items()}

            # Let's get the current task
            if self._validate_task_name == "validate_ae":
                self._test_loader = DataLoader(
                    self._test_dataset,
                    batch_size=self.config["BATCH_SIZE_AE"],
                    shuffle=False,
                    num_workers=1,
                )
                self._n_iterations = len(self._test_loader)
                val_loss, val_ssim = self.do_validation_ae(fl_ctx, weights, abort_signal)
                self.log_info(
                    fl_ctx,
                    f"When validating {model_owner}'s model - Autoencoder - on"
                    f" {fl_ctx.get_identity_name()}"
                    f"s data: SSIM {val_ssim}",
                )

                metrics = {f"metrics_{self._validate_task_name}": {"val_loss": val_loss, "val_ssim": val_ssim}}

            elif self._validate_task_name == "validate_dm":
                self._test_loader = DataLoader(
                    self._test_dataset, batch_size=self.config["BATCH_SIZE_DM"], shuffle=False, num_workers=1
                )
                self._n_iterations = len(self._test_loader)
                val_loss = self.do_validation_dm(fl_ctx, weights, abort_signal)
                self.log_info(
                    fl_ctx,
                    f"When validating {model_owner}'s model - Diffusion model -  on"
                    f" {fl_ctx.get_identity_name()}"
                    f"s data: L1 loss: {val_loss}",
                )
                metrics = {f"metrics_{self._validate_task_name}": {"val_loss": val_loss}}

            if abort_signal.triggered:
                return make_reply(ReturnCode.TASK_ABORTED)

            dxo = DXO(data_kind=DataKind.METRICS, data=metrics)
            self.log_info(fl_ctx, f"Metrics being sent under DXO... {dxo.data}")

            return dxo.to_shareable()

        else:
            return make_reply(ReturnCode.TASK_UNKNOWN)
