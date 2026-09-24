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

"""The autoencoder's reported SSIM, which is only meaningful if its scale assumptions hold.

SSIM carries two assumptions about intensity that are invisible at the call site, and both were
silently wrong once the transform chain moved from ``ScaleIntensityd(0, 1)`` to
``NormalizeIntensityd``:

1. **``data_range``** sets the stability constants (``c1 = (k1 * data_range)^2``, likewise ``c2``).
   Hard-coded to 1 against z-scored volumes whose span is nearer 5.5, it rescales the whole metric.
2. **Clamping the reconstruction to [0, 1]** flattened every voxel above 1 — about 15% of the
   volume, and all of the bright anatomy — to a constant before scoring it.

Together they scored a *perfect* reconstruction at 0.26, so a run reporting 0.20 was already near
the ceiling the metric could award and the number said nothing about image quality. Neither bug
raises, neither shows up in a loss curve, and the failure is not even visible as a suspiciously low
score once you stop expecting 1.0 to be reachable.

The two need different tests, and the obvious one catches neither. A reconstruction identical to
its input scores exactly 1 *whatever* ``data_range`` is — when ``x == y`` the means and variances
coincide and the stability constants cancel — and the clamp lives at the call site, not inside the
metric, so calling the metric directly never sees it. So:

* the clamp is pinned by reading the trainer's source, since it is the caller that has to get it
  right;
* ``data_range`` is pinned by scale invariance — multiply both reconstruction and target by the
  same constant and a derived range gives an identical score, while a hard-coded one drifts. The
  drift is only visible at *small* scales, where the constants are large relative to the signal;
  once the signal dwarfs them the metric approaches its unstabilised limit and is scale-free by
  accident. That narrowness is why this is the second-order bug: on real volumes it moved a
  mild-blur score from 0.730 to 0.765, where the clamp moved it from 0.172 to 0.765.
"""

from __future__ import annotations

import ast
import sys

import pytest
import torch
from tutorial_apps import TUTORIALS_ROOT

APP_FILES = TUTORIALS_ROOT / "nvflare" / "image_synthesis" / "autoencoder" / "app_files"


@pytest.fixture(scope="module")
def foreground_ssim():  # noqa: ANN201 - the trainer module is imported lazily, so it has no import-time type
    """The trainer's own metric, imported the way the app runs it."""
    sys.path.insert(0, str(APP_FILES))
    try:
        from trainer import foreground_ssim as metric
    finally:
        sys.path.remove(str(APP_FILES))
    return metric


def _phantom(seed: int, scale: float = 1.0, size: int = 48) -> torch.Tensor:
    """A z-scored volume with a skull-stripped background, shaped like the real cohort.

    Structured rather than random: SSIM over noise is dominated by the noise, which would make the
    ordering assertions below accidental.
    """
    grid = torch.linspace(-1.0, 1.0, size)
    x, y, z = torch.meshgrid(grid, grid, grid, indexing="ij")
    radius = (x**2 + y**2 + z**2).sqrt()

    generator = torch.Generator().manual_seed(seed)
    shells = torch.sin(radius * 9.0 + float(torch.rand(1, generator=generator)) * 3.0)
    volume = torch.where(radius < 0.8, shells + 2.0, torch.zeros_like(shells))

    volume = volume[None, None]
    volume = (volume - volume.mean()) / volume.std()
    return volume * scale


def _blur(volume: torch.Tensor, width: int) -> torch.Tensor:
    kernel = torch.ones(1, 1, width, width, width) / width**3
    padding = width // 2
    return torch.nn.functional.conv3d(
        torch.nn.functional.pad(volume, (padding,) * 6, mode="replicate"), kernel
    )


def test_the_reconstruction_reaches_the_metric_unclamped(foreground_ssim) -> None:  # noqa: ARG001
    """No caller may clamp or otherwise squash the reconstruction before scoring it.

    This is the bug that mattered. ``NormalizeIntensityd`` z-scores each volume onto roughly
    [-0.4, +5.0]; the inherited ``.clamp(0.0, 1.0)`` flattened everything above 1 — some 15% of the
    volume, and all of the bright anatomy — to a constant, then scored that. It dropped a perfect
    reconstruction to 0.26 and a mild blur from 0.765 to 0.172.

    It has to be checked in the source rather than by calling the metric, because the clamp is the
    caller's, and a metric called directly in a test is never clamped.
    """
    tree = ast.parse((APP_FILES / "trainer.py").read_text())

    offenders = [
        ast.unparse(argument)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "foreground_ssim"
        for argument in node.args
        if any(
            isinstance(inner, ast.Call)
            and (
                (isinstance(inner.func, ast.Attribute) and inner.func.attr in {"clamp", "clip", "sigmoid"})
                or (isinstance(inner.func, ast.Name) and inner.func.id in {"clamp", "clip"})
            )
            for inner in ast.walk(argument)
        )
    ]
    assert not offenders, f"foreground_ssim must be given the raw reconstruction, got: {offenders}"


def test_the_score_does_not_move_with_the_intensity_scale(foreground_ssim) -> None:
    """Scaling both sides by a constant must not change the score, so the range is derived.

    A hard-coded ``data_range`` fixes the stability constants in absolute terms, so the same pair of
    volumes scores differently depending only on the units they arrive in. The small scales are the
    load-bearing ones — see the module docstring.
    """
    scores = {}
    for scale in (0.002, 0.02, 1.0, 500.0):
        volume = _phantom(seed=1, scale=scale)
        scores[scale] = foreground_ssim(_blur(volume, 3), volume)

    spread = max(scores.values()) - min(scores.values())
    assert spread < 1e-4, f"SSIM should not depend on the intensity scale, spread {spread:.2e} over {scores}"


def test_a_perfect_reconstruction_scores_one(foreground_ssim) -> None:
    """A basic sanity property. Note it is insensitive to ``data_range`` — see the module docstring."""
    volume = _phantom(seed=0)
    assert foreground_ssim(volume.clone(), volume) == pytest.approx(1.0, abs=1e-4)


def test_degradations_are_ranked_worst_last(foreground_ssim) -> None:
    """Sharper is better, and the flat-fill silhouette this autoencoder collapses to scores worst.

    The silhouette matters specifically: it is the failure mode L1 cannot distinguish from a good
    reconstruction, so SSIM has to.
    """
    volume = _phantom(seed=4)
    foreground = volume > volume.amin()
    silhouette = torch.where(foreground, volume[foreground].mean(), volume.amin())

    scores = [
        foreground_ssim(volume.clone(), volume),
        foreground_ssim(_blur(volume, 3), volume),
        foreground_ssim(_blur(volume, 7), volume),
        foreground_ssim(silhouette, volume),
    ]
    assert scores == sorted(scores, reverse=True), f"expected monotone degradation, got {scores}"


def test_an_all_background_batch_reports_nan_rather_than_a_score_over_nothing(foreground_ssim) -> None:
    """With no foreground there is nothing to average, and 0.0 would read as a terrible run."""
    empty = torch.full((1, 1, 48, 48, 48), -0.5)
    assert torch.isnan(torch.tensor(foreground_ssim(empty.clone(), empty)))
