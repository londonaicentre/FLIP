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

"""Latent-geometry helpers for the FLIP latent diffusion tutorial.

The diffusion model denoises inside the frozen autoencoder's latent space, so both the training and
the validation pass need to know that space's shape and scale. Those helpers live here because both
passes in ``trainer.py`` share them.

Named ``latent_utils.py`` rather than ``validator.py`` deliberately: this tutorial is a `standard`
job type, whose required upload set is ``trainer.py``/``config.json``/``models.py``. The old
``validator.py`` name belonged to the retired two-stage `diffusion_model` job type, which declared
it as a required file; keeping that name here would imply a contract that no longer applies.
"""

import logging

import torch
from monai.inferers import LatentDiffusionInferer
from monai.networks.schedulers import DDPMScheduler
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


def resolve_scale_factor(
    model: torch.nn.Module,
    sample_images: torch.Tensor,
    device: torch.device,
    configured: float | None = None,
) -> float:
    """Resolve the latent scale factor, preferring a pinned config value over a derived one.

    The diffusion model trains on latents normalised by this factor, so **every site must use the
    same value or their updates are not averaging comparable models.** Deriving it from a local
    batch (the historical behaviour) gives each site a slightly different value; with a frozen
    autoencoder the factor is a fixed property of that autoencoder and the data, so it can simply be
    pinned in ``config.json`` as ``LATENT_SCALE_FACTOR``.

    Args:
        model (torch.nn.Module): The composite LDM network with the autoencoder weights loaded.
        sample_images (torch.Tensor): A batch used to derive the factor when none is configured.
        device (torch.device): Device the model lives on.
        configured (float | None): ``LATENT_SCALE_FACTOR`` from ``config.json``, or None to derive.

    Returns:
        float: The scale factor to hand to :class:`LatentDiffusionInferer`.
    """
    if configured is not None:
        logger.info(f"Latent scale factor: {configured} (pinned via LATENT_SCALE_FACTOR).")
        return float(configured)

    with torch.no_grad():
        with autocast(enabled=True, device_type=device.type):
            sample_z = model.autoencoder.encode_stage_2_inputs(sample_images.to(device))
            scale_factor = 1 / torch.std(sample_z)
            del sample_z

    derived = float(scale_factor.item())
    logger.info(
        f"Latent scale factor: {derived} (derived from a local batch — LATENT_SCALE_FACTOR is unset, "
        "so other sites will derive their own and may differ; pin it in config.json for a "
        "multi-site run)."
    )
    return derived


def build_inferer(
    model: torch.nn.Module,
    scheduler: DDPMScheduler,
    spatial_shape: list,
    sample_images: torch.Tensor,
    device: torch.device,
    scale_factor: float | None = None,
) -> tuple[LatentDiffusionInferer, list]:
    """Build the ``LatentDiffusionInferer`` for the diffusion phase.

    Args:
        model (torch.nn.Module): The composite LDM network with the frozen autoencoder loaded.
        scheduler (DDPMScheduler): The DDPM noise scheduler.
        spatial_shape (list): The input spatial shape from ``config.json``.
        sample_images (torch.Tensor): A batch of images used to infer the latent scale factor when
            none is pinned in the config.
        device (torch.device): Device the model lives on.
        scale_factor (float | None): ``LATENT_SCALE_FACTOR`` from ``config.json``, or None to derive
            it from ``sample_images``.

    Returns:
        tuple[LatentDiffusionInferer, list]: The inferer and the padded LDM latent shape (needed by
        callers to shape the sampling noise).
    """
    autoencoder_latent_shape, ldm_latent_shape = latent_shapes(model, spatial_shape)
    resolved = resolve_scale_factor(model, sample_images, device, scale_factor)

    inferer = LatentDiffusionInferer(
        scheduler=scheduler,
        ldm_latent_shape=ldm_latent_shape,
        autoencoder_latent_shape=autoencoder_latent_shape,
        scale_factor=resolved,
    )
    return inferer, ldm_latent_shape
