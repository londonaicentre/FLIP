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
#

"""
FLIP - Federated Learning and Interoperability Platform

This package provides the core functionality for federated learning in the FLIP platform,
built on NVIDIA FLARE (NVFLARE).

Main exports:
    - FLIP: Factory function that returns the appropriate FLIP implementation based on job type
    - FLIPBase: Abstract base class for FLIP implementations

Example usage:
    from flip import FLIP
    from flip.constants import FlipConstants, ResourceType, ModelStatus

    flip = FLIP()  # Uses default "standard" job type
    df = flip.get_dataframe(project_id, query)
"""

from flip.core.base import FLIPBase
from flip.core.factory import FLIP

__all__ = ["FLIP", "FLIPBase"]

__version__ = "0.1.2"
