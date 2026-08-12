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

"""The tutorial apps under test, and how to reach their transform chains.

Tutorial apps are loose scripts, not packages: an app's ``app_files/`` directory is copied whole
into a job and imported flat, so nothing here is importable by dotted name. Each entry is loaded
from its file path under a unique module name, which also keeps the three same-named
``data_utils`` modules from colliding in ``sys.modules``.

**Scope.** Only apps that read 2-D DICOM through MONAI's ``LoadImaged`` belong here. The spleen and
latent-diffusion tutorials load 3-D NIfTI through ``Orientationd``/``Spacingd``, where the
axis-order correction these tests pin would be actively wrong — they are deliberately absent.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import monai.transforms as mt

TUTORIALS_ROOT = Path(__file__).resolve().parents[1]

# Transforms that only rearrange axes/containers without resampling. The loader prefix is every
# leading transform of this kind; the first transform outside the set ends it. See
# get_loader_prefix for why the boundary is drawn here.
_NON_RESAMPLING_TRANSFORMS: tuple[type, ...] = (
    mt.LoadImaged,
    mt.EnsureChannelFirstd,
    mt.EnsureTyped,
    mt.Lambdad,
)


@dataclass(frozen=True)
class TutorialApp:
    """One tutorial app whose transform chain reads DICOM through MONAI."""

    app_id: str
    backend: str
    module_path: str
    factory: str = "get_xray_transforms"

    @property
    def path(self) -> Path:
        return TUTORIALS_ROOT / self.module_path

    def load_module(self) -> ModuleType:
        """Import the app module from its file path under a unique name.

        Returns:
            ModuleType: The imported module.

        Raises:
            ImportError: If the module cannot be loaded from its path.
        """
        module_name = f"fl_tutorials_under_test.{self.app_id}"
        if module_name in sys.modules:
            return sys.modules[module_name]

        spec = importlib.util.spec_from_file_location(module_name, self.path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {self.path}")
        module = importlib.util.module_from_spec(spec)
        # Register before executing so a module that imports itself indirectly still resolves.
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

    @property
    def has_training_chain(self) -> bool:
        """Whether the app's factory distinguishes a training chain from a validation one.

        The evaluation apps expose a single inference chain — ``get_xray_transforms(input_size)``
        with no ``is_validation`` — because they never train.
        """
        factory = getattr(self.load_module(), self.factory)
        return "is_validation" in inspect.signature(factory).parameters

    def transforms(self, is_validation: bool = True) -> mt.Compose:
        """Return the app's composed transform chain.

        Args:
            is_validation (bool): Passed through to the app's factory where it takes one. Defaults
                to ``True`` so the chain is deterministic — a training chain appends a
                ``RandAffined``. Ignored by the inference-only evaluation apps.

        Returns:
            mt.Compose: The app's own chain, not a reconstruction of it.
        """
        factory = getattr(self.load_module(), self.factory)
        if self.has_training_chain:
            return factory(is_validation=is_validation)
        return factory()


# Every app on the MONAI LoadImaged DICOM path, both backends. Adding a seventh app that loads
# DICOM this way means adding it here — that is the intended maintenance burden.
DICOM_APPS: tuple[TutorialApp, ...] = (
    TutorialApp(
        app_id="nvflare_xray_classification",
        backend="nvflare",
        module_path="nvflare/image_classification/xray_classification/app_files/transforms.py",
    ),
    TutorialApp(
        app_id="nvflare_xray_classification_client_api",
        backend="nvflare",
        module_path="nvflare/image_classification/xray_classification_client_api/app_files/transforms.py",
    ),
    TutorialApp(
        app_id="nvflare_arkplus_fine_tuning",
        backend="nvflare",
        module_path="nvflare/image_classification/arkplus_fine_tuning/app_files/data_utils.py",
    ),
    TutorialApp(
        app_id="nvflare_arkplus_baseline_evaluation",
        backend="nvflare",
        module_path="nvflare/image_evaluation/arkplus_baseline_classification_evaluation/app_files/data_utils.py",
    ),
    TutorialApp(
        app_id="nvflare_arkplus_multimodel_evaluation",
        backend="nvflare",
        module_path="nvflare/image_evaluation/arkplus_multimodel_classification_evaluation/app_files/data_utils.py",
    ),
    TutorialApp(
        app_id="flower_xray_classification",
        backend="flower",
        module_path="flower/xray_classification/app/transforms.py",
    ),
)


def find_load_transform(chain: mt.Compose) -> mt.LoadImaged:
    """Return the single ``LoadImaged`` in a chain.

    Args:
        chain (mt.Compose): A composed transform chain.

    Returns:
        mt.LoadImaged: The chain's loader.

    Raises:
        AssertionError: If the chain does not contain exactly one loader.
    """
    loaders = [t for t in chain.transforms if isinstance(t, mt.LoadImaged)]
    assert len(loaders) == 1, f"expected exactly one LoadImaged in the chain, found {len(loaders)}"
    return loaders[0]


def get_loader_prefix(chain: mt.Compose) -> mt.Compose:
    """Return the leading transforms of ``chain`` that run before anything resamples the image.

    Comparing the *whole* chain against ``PixelData`` is not implementable: the chain's output is
    resized, rescaled and (for the Ark+ apps) ImageNet-normalised float32 with three channels,
    while the reference is a 2-D uint16 array. Cutting the chain at the first resampling transform
    leaves a prefix whose output is still the loaded pixels — comparable bit-for-bit, and the part
    of the chain that decides orientation.

    Args:
        chain (mt.Compose): A composed transform chain.

    Returns:
        mt.Compose: The prefix, itself composed.

    Raises:
        AssertionError: If the chain is entirely prefix, i.e. nothing resamples. That would mean
            the boundary this function assumes no longer exists, and the caller's comparison would
            silently be against something other than a loader prefix.
    """
    prefix = []
    for transform in chain.transforms:
        if not isinstance(transform, _NON_RESAMPLING_TRANSFORMS):
            break
        prefix.append(transform)
    else:  # pragma: no cover - defensive; every app chain resizes
        raise AssertionError("no resampling transform found: the chain is not shaped as expected")

    assert prefix, "chain does not begin with a loader"
    return mt.Compose(prefix)
