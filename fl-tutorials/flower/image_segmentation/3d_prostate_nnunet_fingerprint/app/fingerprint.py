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

"""nnU-Net's dataset fingerprint as histograms a size-weighted average can pool.

nnU-Net plans a network from a *fingerprint* of the whole dataset: every case's voxel spacing and
cropped shape, a sample of its foreground intensities, and how much cropping removed. The planner
reduces those to per-axis quantiles and a case count, so a site does not have to send its cases:
it sends, per axis, the share of its cases at each spacing, each shape and each physical extent
(shape x spacing), plus its mean per-case intensity histogram. Weighted by case count, the mean of
those shares over the sites is the pooled dataset's own histogram (FedAvg, unchanged), and
:func:`pooled_fingerprint` turns it back into the lists the planner reads.

Values are binned by rounding to a fixed grid from ``config.json`` (bin ``k`` holds ``k * width``),
so a spacing of exactly 0.5 mm or an integer shape survives the round trip unchanged; anything else
is accurate to half a bin. Axes are in nnU-Net's ``(d, h, w)`` order throughout.

No site knows the pooled case count, which the planner needs. Each site writes its own count into
one slot of ``site_cases``; after size-weighted averaging slot ``j`` holds ``n_j^2 / N``, and
``N = (sum_j sqrt(slot_j))^2`` recovers the total (the causal-discovery tutorial's device).
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass

import numpy as np
from scipy.ndimage import binary_fill_holes

logger = logging.getLogger(__name__)

STATE_KEYS = ("site_cases", "spacing", "shape", "extent", "intensity", "relative_size")
#: Slots in ``site_cases``: a site's slot is its index (Trust_1 -> 0), or a hash of its name.
MAX_SITES = 64
#: Relative size after cropping lies in (0, 1]; binned to 0.001.
RELATIVE_SIZE_BINS = 1001
#: No site releases a fingerprint of fewer cases than this; ``MIN_SITE_CASES`` can only raise it.
HARD_MIN_SITE_CASES = 10


@dataclass(frozen=True)
class CaseFingerprint:
    """What nnU-Net's ``analyze_case`` keeps of one case."""

    spacing: tuple[float, float, float]
    shape_after_crop: tuple[int, int, int]
    relative_size_after_cropping: float
    foreground_intensities: list[np.ndarray]  # one array per image channel


def analyze_case(image: np.ndarray, foreground: np.ndarray, spacing: tuple[float, float, float]) -> CaseFingerprint:
    """Crop one case to its non-zero region and collect what the fingerprint needs.

    The cropping is nnU-Net's ``crop_to_nonzero``: the bounding box of the voxels that are non-zero
    in any channel, holes filled. Its foreground intensities are those under the label inside that
    box. nnU-Net samples a fixed number of them per case; here all are kept and each case's
    histogram is normalised instead, which weights the cases the same way without the sampling.

    Args:
        image: ``(C, d, h, w)`` image.
        foreground: ``(d, h, w)`` boolean label mask.
        spacing: Voxel spacing in mm, ``(d, h, w)``.

    Returns:
        The case's fingerprint.
    """
    nonzero = binary_fill_holes(np.any(image != 0, axis=0))
    if not nonzero.any():
        raise ValueError("a case has an all-zero image")
    box = tuple(slice(int(hits.min()), int(hits.max()) + 1) for hits in np.nonzero(nonzero))
    cropped = image[(slice(None), *box)]
    return CaseFingerprint(
        spacing=tuple(float(s) for s in spacing),
        shape_after_crop=cropped.shape[1:],
        relative_size_after_cropping=float(np.prod(cropped.shape[1:]) / np.prod(image.shape[1:])),
        foreground_intensities=[channel[foreground[box]] for channel in cropped],
    )


def _bins(maximum: float, width: float) -> int:
    return int(round(maximum / width)) + 1


def state_shapes(config: dict) -> dict[str, tuple[int, ...]]:
    """Shape of each exchanged array, fixed by ``config.json`` alone."""
    return {
        "site_cases": (MAX_SITES,),
        "spacing": (3, _bins(config["MAX_SPACING_MM"], config["SPACING_BIN_MM"])),
        "shape": (3, int(config["MAX_SHAPE"]) + 1),
        "extent": (3, _bins(config["MAX_EXTENT_MM"], config["EXTENT_BIN_MM"])),
        "intensity": (
            int(config["NUM_CHANNELS"]),
            _bins(config["INTENSITY_MAX"] - config["INTENSITY_MIN"], config["INTENSITY_BIN"]),
        ),
        "relative_size": (RELATIVE_SIZE_BINS,),
    }


def site_slot(site_name: str) -> int:
    """Which ``site_cases`` slot this site writes: its index when the name ends in one."""
    match = re.search(r"(\d+)\s*$", site_name or "")
    if match:
        return (int(match.group(1)) - 1) % MAX_SITES
    return int(hashlib.sha256((site_name or "").encode()).hexdigest(), 16) % MAX_SITES


def assert_site_may_release(num_cases: int, config: dict, site_name: str = "") -> None:
    """Refuse below the case floor. The message carries no count: Flower forwards it to the hub."""
    floor = max(HARD_MIN_SITE_CASES, int(config.get("MIN_SITE_CASES", 0)))
    if num_cases < floor:
        logger.info("site %s: %d usable cases, below the release floor of %d", site_name, num_cases, floor)
        raise ValueError("this site has too few usable cases to release a fingerprint (see the site's own log)")


def site_statistics(cases: list[CaseFingerprint], config: dict, site_name: str) -> dict[str, np.ndarray]:
    """One site's arrays: its case count in its slot, and the share of its cases in every bin.

    A spacing, shape or extent beyond the configured range is refused, since clipping it would
    move the quantiles the plan is built from. Intensities are clipped into the end bins: MR
    outliers are common and only shift the reported extremes.
    """
    shapes = state_shapes(config)
    stats = {key: np.zeros(shape, dtype=np.float64) for key, shape in shapes.items()}
    stats["site_cases"][site_slot(site_name)] = len(cases)
    intensity_bins = shapes["intensity"][1]

    for case in cases:
        extent = np.asarray(case.shape_after_crop) * np.asarray(case.spacing)
        binned = {
            "spacing": np.rint(np.asarray(case.spacing) / config["SPACING_BIN_MM"]).astype(int),
            "shape": np.asarray(case.shape_after_crop, dtype=int),
            "extent": np.rint(extent / config["EXTENT_BIN_MM"]).astype(int),
        }
        for key, indices in binned.items():
            if np.any(indices >= shapes[key][1]):
                logger.info("site %s: a case's %s %s is beyond the configured range", site_name, key, indices)
                raise ValueError("a case falls outside the histogram ranges in config.json (see the site's own log)")
            stats[key][np.arange(3), indices] += 1
        stats["relative_size"][int(round(case.relative_size_after_cropping * (RELATIVE_SIZE_BINS - 1)))] += 1
        for channel, values in enumerate(case.foreground_intensities):
            if len(values):
                indices = np.rint((values - config["INTENSITY_MIN"]) / config["INTENSITY_BIN"]).astype(int)
                counts = np.bincount(np.clip(indices, 0, intensity_bins - 1), minlength=intensity_bins)
                stats["intensity"][channel] += counts / len(values)

    for key in ("spacing", "shape", "extent", "intensity", "relative_size"):
        stats[key] /= len(cases)
    return {key: value.astype(np.float32) for key, value in stats.items()}


def pooled_case_count(site_cases: np.ndarray) -> int:
    """The pooled case count from the size-weighted mean of the sites' ``site_cases``."""
    return int(round(float(np.sum(np.sqrt(np.asarray(site_cases, dtype=np.float64)))) ** 2))


def _expand(shares: np.ndarray, num_cases: int) -> np.ndarray:
    """Bin indices of ``num_cases`` cases, sorted, from the share of cases in each bin."""
    expected = np.asarray(shares, dtype=np.float64) * num_cases
    counts = np.rint(expected).astype(int)
    # Shares of a whole number of cases, or the arrays are not what the sites computed.
    if counts.sum() != num_cases or np.abs(expected - counts).max() > 0.05:
        raise ValueError(
            "the histograms do not add up to the pooled case count: was this model produced with dp-enabled = false, "
            "by sites whose names map to different site_cases slots?"
        )
    return np.repeat(np.arange(len(counts)), counts)


def _intensity_properties(shares: np.ndarray, config: dict) -> dict[str, float]:
    """nnU-Net's per-channel foreground statistics, read off the pooled histogram."""
    density = np.asarray(shares, dtype=np.float64)
    density = density / density.sum()
    values = config["INTENSITY_MIN"] + np.arange(len(density)) * config["INTENSITY_BIN"]
    cumulative = np.cumsum(density)
    mean = float(np.sum(density * values))

    def quantile(q: float) -> float:
        return float(values[min(int(np.searchsorted(cumulative, q)), len(values) - 1)])

    return {
        "mean": mean,
        "median": quantile(0.5),
        "std": float(np.sqrt(np.sum(density * (values - mean) ** 2))),
        "min": float(values[np.flatnonzero(density)[0]]),
        "max": float(values[np.flatnonzero(density)[-1]]),
        "percentile_99_5": quantile(0.995),
        "percentile_00_5": quantile(0.005),
    }


def pooled_fingerprint(state: dict[str, np.ndarray], config: dict) -> dict:
    """The planner's ``dataset_fingerprint.json`` from the pooled arrays.

    ``spacings`` and ``shapes_after_crop`` come back as one row per case, but each axis is sorted
    on its own, so a row is not a case: per-axis quantiles are right and nothing else is. The one
    place the planner needs spacing and shape of the *same* case, the median shape after
    resampling, is served by ``physical_extents`` (also sorted per axis).
    """
    num_cases = pooled_case_count(state["site_cases"])
    if num_cases == 0:
        raise ValueError("the model holds no fingerprint (site_cases is empty)")

    def per_case(key: str, width: float) -> np.ndarray:
        return np.stack([_expand(state[key][axis], num_cases) * width for axis in range(3)], axis=1)

    relative_sizes = _expand(state["relative_size"], num_cases) / (RELATIVE_SIZE_BINS - 1)
    return {
        "spacings": per_case("spacing", config["SPACING_BIN_MM"]).tolist(),
        "shapes_after_crop": per_case("shape", 1).astype(int).tolist(),
        "physical_extents": per_case("extent", config["EXTENT_BIN_MM"]).tolist(),
        "foreground_intensity_properties_per_channel": {
            str(channel): _intensity_properties(shares, config) for channel, shares in enumerate(state["intensity"])
        },
        "median_relative_size_after_cropping": float(np.median(relative_sizes)),
    }
