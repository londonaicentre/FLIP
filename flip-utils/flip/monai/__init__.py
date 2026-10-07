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

"""MONAI bundle on-ramp: train a MONAI training bundle on FLIP (FLIP#1104).

This is the *inbound* direction: a researcher's MONAI training bundle plus a ``monai-flip.yaml``
overlay. It is distinct from :mod:`flip.export`, which writes *inference* bundles out of a finished
run; an exported bundle has no ``configs/train.json`` and cannot be fed back in.

Imports neither torch nor MONAI, and must not import :mod:`flip.export` (which requires torch).
"""

from flip.monai.overlay import BundleSource, MonaiFlipOverlay, OverlayError, load_overlay, parse_overlay
from flip.monai.skeleton import SkeletonError, generate_skeleton

__all__ = [
    "BundleSource",
    "MonaiFlipOverlay",
    "OverlayError",
    "SkeletonError",
    "generate_skeleton",
    "load_overlay",
    "parse_overlay",
]
