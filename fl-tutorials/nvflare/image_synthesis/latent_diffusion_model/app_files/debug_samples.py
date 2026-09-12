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

"""Client-local debug images for the image-synthesis tutorials.

A generative model is the one kind of model whose scalars barely tell you whether it is working: an
autoencoder's L1 falls steadily while it learns to emit a blur, and a diffusion model's MSE on
predicted noise is almost uninformative about sample quality. This module writes the pixels
themselves, so a local tutorial run can be judged by looking at it.

**Nothing here puts an image on the wire.** The files are written beside the running training script
— i.e. inside that client's own job workspace — and no code path reads them back, adds them to an
``FLModel``, or hands them to the metrics writer (``SummaryWriter`` could not carry them anyway: the
Client API's writer exposes only ``add_scalar``/``add_scalars``). An image saved here leaves the site
only if a human deliberately copies it out, which at a real trust is a disclosure decision governed
by the same rules as any other data egress, not something a training run can do by itself.

Off by default, and enabled per job by ``SAVE_DEBUG_SAMPLES`` in ``config.json``. Treat it as a
local-development instrument: leaving it on at a trust accumulates patient-derived reconstructions
on that trust's disk, round after round, with no retention policy attached.
"""

import logging
from pathlib import Path

import torch
from torchvision.utils import make_grid, save_image

logger = logging.getLogger(__name__)

#: Directory created next to the training script. Gitignored, and never read back by any code path.
DEBUG_DIR_NAME = "debug_samples"

#: How many images to write per grid when ``DEBUG_SAMPLES_MAX`` is absent from the config.
DEFAULT_MAX_IMAGES = 8


def samples_enabled(config: dict) -> bool:
    """Whether this job asked for client-local debug images.

    Absent key means off, so an existing ``config.json`` keeps its current behaviour.

    Args:
        config (dict): The parsed ``config.json``.

    Returns:
        bool: True when ``SAVE_DEBUG_SAMPLES`` is truthy.
    """
    return bool(config.get("SAVE_DEBUG_SAMPLES", False))


def debug_dir() -> Path:
    """Return (creating if needed) the debug directory beside this app's training script.

    Resolved from ``__file__`` rather than the process working directory, which for an NVFLARE client
    is the workspace root shared by every app in the run. Each client unpacks its own copy of the app,
    so this path is inherently per-client and per-run — two simulated sites cannot overwrite each
    other's images.
    """
    target = Path(__file__).parent.resolve() / DEBUG_DIR_NAME
    target.mkdir(parents=True, exist_ok=True)
    return target


def _to_2d(batch: torch.Tensor) -> torch.Tensor:
    """Reduce a batch to something ``make_grid`` can tile, taking a mid-slice of any volume.

    Args:
        batch (torch.Tensor): ``(B, C, H, W)`` or ``(B, C, H, W, D)``.

    Returns:
        torch.Tensor: ``(B, C, H, W)`` — volumes sliced at the centre of the last axis, which for the
        RAS-oriented volumetric chain is the axial mid-slice.
    """
    if batch.ndim == 5:
        return batch[..., batch.shape[-1] // 2]
    return batch


def _prepare(batch: torch.Tensor, max_images: int) -> torch.Tensor:
    """Detach, truncate and flatten a batch to greyscale 2-D images ready for tiling."""
    prepared = _to_2d(batch.detach().float().cpu()[:max_images])
    if prepared.shape[1] > 1:
        # Multi-channel here means a latent, not a colour image; show the first channel rather than
        # letting save_image interpret 3 channels as RGB.
        prepared = prepared[:, :1]
    return prepared


def save_grid(
    tensors: dict[str, torch.Tensor],
    name: str,
    config: dict,
    *,
    site_name: str = "",
    step: int | None = None,
) -> Path | None:
    """Write one PNG tiling the given batches, one row per entry. No-op unless enabled.

    Rows are normalised **jointly**, not per image: independent scaling would rescale a washed-out
    reconstruction back onto its input's range and hide precisely the intensity drift the comparison
    is meant to expose.

    Never raises. Debug output must not be able to fail a training round, so any error (a read-only
    workspace, a tensor of an unexpected rank) is logged and swallowed.

    Args:
        tensors (dict[str, torch.Tensor]): Batches to tile, one row each, in insertion order — e.g.
            ``{"input": images, "reconstruction": recon}``. Keys appear in the filename.
        name (str): Short label for what this grid shows, e.g. ``"reconstruction"``.
        config (dict): The parsed ``config.json``; read for ``SAVE_DEBUG_SAMPLES``/``DEBUG_SAMPLES_MAX``.
        site_name (str): Client name, included in the filename so a shared workspace cannot collide.
        step (int | None): Round or epoch counter, included in the filename to order the images.

    Returns:
        Path | None: The file written, or None when disabled or on any failure.
    """
    if not samples_enabled(config) or not tensors:
        return None

    try:
        max_images = int(config.get("DEBUG_SAMPLES_MAX", DEFAULT_MAX_IMAGES))
        rows = [_prepare(batch, max_images) for batch in tensors.values()]
        columns = min(row.shape[0] for row in rows)
        grid = make_grid(
            torch.cat([row[:columns] for row in rows]),
            nrow=columns,
            normalize=True,
            scale_each=False,
        )

        parts = [name, *tensors.keys()] if len(tensors) > 1 else [name]
        if site_name:
            parts.append(site_name)
        if step is not None:
            parts.append(f"step{step:04d}")
        path = debug_dir() / ("_".join(parts) + ".png")

        save_image(grid, str(path))
        logger.info(f"[DEBUG] Wrote {columns} sample(s) to {path}")
        return path
    except Exception as err:
        logger.warning(f"[DEBUG] Could not write debug samples {name!r}: {err}")
        return None
