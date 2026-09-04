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

"""Nuclei detection on H&E tiles, and the swappable detector interface around it.

The tutorial ships a **classical** detector rather than a deep model, deliberately:

* It needs no dependency FLIP's FL runtime does not already carry (scikit-image and scipy arrive with
  monai[skimage,scipy]), so the tutorial runs on the published FL images with no rebuild.
* The tutorial is about the federated-evaluation architecture, not about winning a detection
  benchmark. A weak-but-honest detector makes the cross-site comparison *more* legible, because the
  differences between sites are large enough to see.

Swapping in Cellpose, HoVer-Net or StarDist means adding a NucleiDetector implementation and
naming it in config.json; nothing else in the tutorial changes. Those models do need new runtime
dependencies, which is why they are not the default.

Detector parameters are expressed in **micrometres**, not pixels, and converted per slide using its
pixel spacing. Slides in this collection differ in magnification (0.2325 vs 0.2470 um/px among the
tutorial's own subset), so a pixel-valued min_distance would silently mean different things at
different sites — precisely the kind of artefact that would masquerade as a site effect.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np
from skimage.color import rgb2hed
from skimage.feature import peak_local_max
from skimage.filters import gaussian
from skimage.morphology import disk, white_tophat


@dataclass(frozen=True)
class DetectorParameters:
    """Physical, scale-invariant parameters for the classical detector.

    Attributes:
        nucleus_diameter_um: Typical nucleus diameter. Sets the minimum separation between detections,
            so it controls how aggressively touching nuclei are merged.
        smoothing_sigma_um: Gaussian smoothing applied to the haematoxylin channel before peak finding.
        threshold_sigma: Peak floor, in standard deviations above the tile's own mean background
            response. An absolute-but-adaptive floor rather than a fraction of the tile maximum:
            a fraction-of-maximum floor is set by the single darkest object in the tile, so one
            dense nucleus or a fold silently suppresses every other detection in that tile.
        background_disk_um: Radius of the white-tophat structuring element, as a multiple of nothing
            -- it is an absolute size, and must exceed a nucleus so the filter removes slowly varying
            background while leaving nuclei intact.
        min_tissue_fraction: Tiles with less tissue than this are skipped before detection runs.
    """

    nucleus_diameter_um: float = 6.0
    smoothing_sigma_um: float = 0.5
    threshold_sigma: float = 1.5
    background_disk_um: float = 9.0
    min_tissue_fraction: float = 0.10


class NucleiDetector(Protocol):
    """The interface the evaluator depends on. Any detector implementing it can be dropped in."""

    def predict(self, tile_pixels: np.ndarray, micrometres_per_pixel: float) -> np.ndarray:
        """Return an (N, 2) array of predicted (x, y) nucleus centres, tile-local."""
        ...


def tissue_fraction(tile_pixels: np.ndarray) -> float:
    """Fraction of a tile that looks like stained tissue rather than blank glass.

    Glass is bright and colourless; stained tissue is darker and more saturated. Requiring *both*
    rejects the two things that fool either test alone — bright pink tissue folds, and dim grey
    scanner vignetting at a slide's edge.
    """
    if tile_pixels.size == 0:
        return 0.0
    pixels = tile_pixels.astype(np.int16)
    intensity = pixels.max(axis=2)
    saturation = pixels.max(axis=2) - pixels.min(axis=2)
    return float(((intensity < 220) & (saturation > 20)).mean())


class HaematoxylinPeakDetector:
    """Detect nuclei as local maxima of the haematoxylin channel of an H&E tile.

    Haematoxylin stains nucleic acid, so separating it from eosin with colour deconvolution
    (skimage.color.rgb2hed) gives a channel where nuclei are bright blobs on a dark field.
    Smoothing then suppresses chromatin texture, which would otherwise split one nucleus into several
    peaks, and peak_local_max with a minimum separation of roughly one nucleus diameter picks the
    centres.
    """

    def __init__(self, parameters: DetectorParameters | None = None) -> None:
        self.parameters = parameters or DetectorParameters()

    def predict(self, tile_pixels: np.ndarray, micrometres_per_pixel: float) -> np.ndarray:
        """Detect nuclei in one RGB tile.

        Args:
            tile_pixels: (rows, columns, 3) uint8 RGB tile.
            micrometres_per_pixel: Physical scale, used to convert the physical parameters to pixels.

        Returns:
            (N, 2) array of tile-local (x, y) centres. Empty when the tile is mostly glass.
        """
        if micrometres_per_pixel <= 0:
            raise ValueError(f"micrometres_per_pixel must be positive, got {micrometres_per_pixel!r}.")
        if tile_pixels.ndim != 3 or tile_pixels.shape[2] != 3:
            raise ValueError(f"Expected an RGB tile of shape (rows, columns, 3), got {tile_pixels.shape}.")
        if tissue_fraction(tile_pixels) < self.parameters.min_tissue_fraction:
            return np.empty((0, 2), dtype=float)

        haematoxylin = rgb2hed(tile_pixels)[..., 0]

        # Flatten slowly varying background before thresholding. Staining intensity drifts across a
        # slide and tissue folds are broadly dark, so without this the threshold is set by regional
        # brightness rather than by nuclei, and whole tiles come back empty or full of noise.
        disk_px = max(int(round(self.parameters.background_disk_um / micrometres_per_pixel)), 3)
        flattened = white_tophat(haematoxylin, disk(disk_px))

        sigma_px = max(self.parameters.smoothing_sigma_um / micrometres_per_pixel, 1e-3)
        smoothed = gaussian(flattened, sigma=sigma_px)

        # A nucleus diameter is the closest two distinct centres should ever be.
        min_distance_px = max(int(round(self.parameters.nucleus_diameter_um / micrometres_per_pixel)), 1)
        threshold = float(smoothed.mean() + self.parameters.threshold_sigma * smoothed.std())
        peaks = peak_local_max(
            smoothed,
            min_distance=min_distance_px,
            threshold_abs=threshold,
            exclude_border=False,
        )
        if len(peaks) == 0:
            return np.empty((0, 2), dtype=float)
        # peak_local_max returns (row, column); the rest of the tutorial works in (x, y).
        return peaks[:, ::-1].astype(float)
