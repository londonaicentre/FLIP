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

"""Cross-site validation passes and latent-geometry helpers for the latent diffusion tutorial.

Ported from the legacy Executor-based ``FLIP_VALIDATOR``: the ``Executor``/``Shareable`` plumbing is
gone — ``trainer.py`` (the Client API script) receives the ``validate_ae`` / ``validate_dm`` tasks,
loads the broadcast global weights, and calls :func:`validate_ae` / :func:`validate_dm` here. The
latent-geometry helpers (:func:`derive_new_latent_shape`, :func:`build_inferer`) live here because
the training and validation passes of the diffusion phase share them.
"""

import logging

import numpy as np
import torch
from flip.constants import FlipConstants
from monai.data import DataLoader
from monai.inferers import LatentDiffusionInferer
from monai.metrics import compute_ssim_and_cs
from monai.networks.schedulers import DDPMScheduler
from nvflare.client.tracking import SummaryWriter
from torch.amp import autocast

logger = logging.getLogger(__name__)


def derive_new_latent_shape(input_shape: list, num_downsamplings: int) -> list:
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


def latent_shapes(model: torch.nn.Module, spatial_shape: list) -> tuple[list, list]:
    """Infer the autoencoder latent shape and the diffusion-model-compatible (padded) latent shape.

    Args:
        model (torch.nn.Module): The composite LDM network (``models.get_model()``).
        spatial_shape (list): The input spatial shape from ``config.json``.

    Returns:
        tuple[list, list]: ``(autoencoder_latent_shape, ldm_latent_shape)``.
    """
    autoencoder_latent_shape = [i / (2 ** (len(model.autoencoder.decoder.channels) - 1)) for i in spatial_shape]
    ldm_latent_shape = derive_new_latent_shape(
        autoencoder_latent_shape, len(model.diffusion_model.block_out_channels) - 1
    )
    return autoencoder_latent_shape, ldm_latent_shape


def build_inferer(
    model: torch.nn.Module,
    scheduler: DDPMScheduler,
    spatial_shape: list,
    sample_images: torch.Tensor,
    device: torch.device,
) -> tuple[LatentDiffusionInferer, list]:
    """Build the ``LatentDiffusionInferer`` for the diffusion phase.

    The scale factor is inferred from a sample batch encoded through the (frozen) autoencoder, as in
    the legacy trainer/validator.

    Args:
        model (torch.nn.Module): The composite LDM network with trained autoencoder weights loaded.
        scheduler (DDPMScheduler): The DDPM noise scheduler.
        spatial_shape (list): The input spatial shape from ``config.json``.
        sample_images (torch.Tensor): A batch of images used to infer the latent scale factor.
        device (torch.device): Device the model lives on.

    Returns:
        tuple[LatentDiffusionInferer, list]: The inferer and the padded LDM latent shape (needed by
        callers to shape the sampling noise).
    """
    autoencoder_latent_shape, ldm_latent_shape = latent_shapes(model, spatial_shape)

    with torch.no_grad():
        with autocast(enabled=True, device_type=device.type):
            sample_z = model.autoencoder.encode_stage_2_inputs(sample_images.to(device))
            scale_factor = 1 / torch.std(sample_z)
            del sample_z

    inferer = LatentDiffusionInferer(
        scheduler=scheduler,
        ldm_latent_shape=ldm_latent_shape,
        autoencoder_latent_shape=autoencoder_latent_shape,
        scale_factor=scale_factor.item(),
    )
    return inferer, ldm_latent_shape


def validate_ae(
    model: torch.nn.Module,
    test_loader: DataLoader,
    device: torch.device,
    writer: SummaryWriter,
) -> tuple[float, float]:
    """Score the aggregated autoencoder on the local held-out split.

    Args:
        model (torch.nn.Module): The composite LDM network with the broadcast global weights loaded.
        test_loader (DataLoader): Held-out data loader.
        device (torch.device): Device to run on.
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

    for batch in test_loader:
        images = batch["image"].to(device)

        with torch.no_grad():
            reconstruction, _, _ = model.autoencoder(images)
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

    return l1_test_loss, ssim_test


def validate_dm(
    model: torch.nn.Module,
    test_loader: DataLoader,
    scheduler: DDPMScheduler,
    config: dict,
    device: torch.device,
    writer: SummaryWriter,
) -> float:
    """Score the aggregated diffusion model on the local held-out split.

    Args:
        model (torch.nn.Module): The composite LDM network with the broadcast global weights loaded.
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
    model.autoencoder.to(device=device)
    inferer, ldm_latent_shape = build_inferer(
        model, scheduler, config["spatial_shape"], next(iter(test_loader))["image"], device
    )

    model.diffusion_model.eval()
    model.autoencoder.eval()

    val_loss = []
    for batch in test_loader:
        images = batch["image"].to(device)
        with autocast(enabled=False, device_type=device.type):
            with torch.no_grad():
                noise = torch.randn(
                    [images.shape[0]] + [model.autoencoder.encoder.blocks[-1].out_channels] + ldm_latent_shape
                ).to(device)
                timesteps = torch.randint(
                    0, scheduler.num_train_timesteps, (images.shape[0],), device=device
                ).long()
                logger.info(f"Sizes: images = {images.shape}, noise = {noise.shape}")
                noise_pred = inferer(
                    inputs=images,
                    diffusion_model=model.diffusion_model,
                    autoencoder_model=model.autoencoder,
                    noise=noise,
                    timesteps=timesteps,
                    condition=None,
                    mode="crossattn",
                )
            val_loss.append(loss_fn(noise.float(), noise_pred.float()).item())

    if FlipConstants.LOCAL_DEV:
        # Dev-only sanity check that end-to-end sampling works with the aggregated weights.
        logger.info("[DEV]: Sampling images...")
        noise = torch.randn(
            [config["BATCH_SIZE_DM"]] + [model.autoencoder.encoder.blocks[-1].out_channels] + ldm_latent_shape
        ).to(device)
        sampled_images, _ = inferer.sample(
            input_noise=noise,
            conditioning=None,
            diffusion_model=model.diffusion_model,
            scheduler=scheduler,
            save_intermediates=True,
            autoencoder_model=model.autoencoder,
        )
        logger.info(f"Sampled images shape: {sampled_images.detach().cpu().numpy().shape}")

    mean_val_loss = float(np.mean(val_loss)) if val_loss else float("nan")
    logger.info(f"Validation DM: {mean_val_loss}")

    writer.add_scalar("Total loss DM (val)", mean_val_loss, global_step=0)

    return mean_val_loss
