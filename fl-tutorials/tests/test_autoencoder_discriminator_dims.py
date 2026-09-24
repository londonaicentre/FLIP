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

"""The discriminator's dimensionality is a configuration choice, and both choices must keep working.

``net_config.discriminator.spatial_dims`` selects between scoring whole volumes (3) and scoring
randomly chosen axial slices (2). Neither is universally right: a 3-D discriminator sees
through-plane structure that a slice cannot show, while on genuinely thick-slice data a 2-D one
compares voxels that are actually comparable — which is why the choice is exposed rather than
fixed.

The hazard is that only one of the two is exercised by the shipped config, so the other rots
unnoticed. It rots *silently*: the trainer selects the path with an ``== 2`` test, so a broken or
unreachable branch does not raise, it simply trains the wrong thing — and an adversarial loss that
is quietly scoring the wrong tensor still produces a plausible, declining curve.

These tests therefore run both paths through the same call the trainer makes, and check the
adversarial signal actually reaches the reconstruction. Being CPU-only they build the
discriminator alone rather than the full network; the autoencoder's own dimensionality is pinned
separately by the config-parity suite.
"""

from __future__ import annotations

import copy
import json
import sys

import numpy as np
import pytest
import torch
from monai.losses import PatchAdversarialLoss
from monai.networks.nets import PatchDiscriminator
from tutorial_apps import TUTORIALS_ROOT

APP_FILES = TUTORIALS_ROOT / "nvflare" / "image_synthesis" / "autoencoder" / "app_files"

# The smallest shape that survives `num_layers_d` halvings and still exceeds the 4-voxel kernel
# that follows them. The trainer runs at 96^3; nothing here depends on the exact value.
SPATIAL_SHAPE = (64, 64, 64)
BATCH = 2


@pytest.fixture(scope="module")
def discriminator_config() -> dict:
    """The shipped discriminator block, so these tests track the real configuration."""
    return json.loads((APP_FILES / "config.json").read_text())["net_config"]["discriminator"]


@pytest.fixture(scope="module")
def slice_volume_for_2d():  # noqa: ANN201 - imported lazily from the app dir
    """The trainer's own slicing helper, so the 2-D path is tested as the trainer runs it."""
    sys.path.insert(0, str(APP_FILES))
    try:
        from trainer import slice_volume_for_2d as helper
    finally:
        sys.path.remove(str(APP_FILES))
    return helper


def _build(discriminator_config: dict, spatial_dims: int) -> PatchDiscriminator:
    config = copy.deepcopy(discriminator_config)
    config["spatial_dims"] = spatial_dims
    return PatchDiscriminator(
        spatial_dims=config["spatial_dims"],
        in_channels=config["in_channels"],
        channels=config["channels"],
        out_channels=config["out_channels"],
        num_layers_d=config["num_layers_d"],
    )


def _adversarial_gradient(discriminator_config: dict, spatial_dims: int, slice_volume_for_2d) -> float:  # noqa: ANN001
    """Run the trainer's generator-side adversarial term and return the gradient reaching the input."""
    torch.manual_seed(0)
    discriminator = _build(discriminator_config, spatial_dims)
    reconstruction = torch.randn(BATCH, 1, *SPATIAL_SHAPE, requires_grad=True)

    if spatial_dims == 2:
        indices = np.random.randint(0, SPATIAL_SHAPE[-1], size=4)
        scored = slice_volume_for_2d(reconstruction, indices).contiguous().float()
    else:
        scored = reconstruction.contiguous().float()

    logits = discriminator(scored)[-1]
    loss = PatchAdversarialLoss(criterion="least_squares")(logits, target_is_real=True, for_discriminator=False)
    loss.backward()

    assert torch.isfinite(loss), f"adversarial loss was not finite at spatial_dims={spatial_dims}"
    return reconstruction.grad.abs().sum().item()


@pytest.mark.parametrize("spatial_dims", [2, 3])
def test_both_discriminator_dimensionalities_train_the_autoencoder(
    discriminator_config, spatial_dims, slice_volume_for_2d
) -> None:
    """Whichever is configured, the adversarial term must reach the reconstruction with a gradient.

    A zero gradient is the failure that matters: it means the discriminator is attached but
    contributing nothing, which no loss curve distinguishes from a discriminator that is merely
    doing badly.
    """
    gradient = _adversarial_gradient(discriminator_config, spatial_dims, slice_volume_for_2d)
    assert gradient > 0.0, f"the adversarial term reached the reconstruction with no gradient at {spatial_dims}-D"


@pytest.mark.parametrize("spatial_dims", [2, 3])
def test_the_discriminator_uses_convolutions_of_the_configured_rank(discriminator_config, spatial_dims) -> None:
    """The built network must actually be n-D, not a 3-D one fed flattened slices (or the reverse).

    ``PatchDiscriminator`` accepts ``spatial_dims`` without complaint, so the only way to know the
    key was honoured is to look at the convolutions it built.
    """
    expected = {2: torch.nn.Conv2d, 3: torch.nn.Conv3d}[spatial_dims]
    wrong = {2: torch.nn.Conv3d, 3: torch.nn.Conv2d}[spatial_dims]

    modules = list(_build(discriminator_config, spatial_dims).modules())
    assert any(isinstance(module, expected) for module in modules)
    assert not any(isinstance(module, wrong) for module in modules)


def test_the_two_dimensionalities_are_not_the_same_network(discriminator_config, slice_volume_for_2d) -> None:
    """A sanity check that the parametrisation above is testing two genuinely different things.

    If a future refactor made ``spatial_dims`` inert, every test above would still pass while the
    config key silently stopped meaning anything. The parameter counts differ by roughly the
    kernel's extra dimension, so comparing them catches that.
    """
    counts = {
        dims: sum(parameter.numel() for parameter in _build(discriminator_config, dims).parameters())
        for dims in (2, 3)
    }
    assert counts[3] > counts[2], f"a 3-D discriminator should carry more parameters, got {counts}"


def test_the_shipped_config_selects_a_dimensionality_the_trainer_implements(discriminator_config) -> None:
    """Only 2 and 3 have a branch in the trainer; anything else would silently take the 3-D path."""
    assert discriminator_config["spatial_dims"] in (2, 3)
