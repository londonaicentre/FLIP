# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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
FLIP Core module containing base classes and implementations.

Exports:
    - FLIPBase: Abstract base class for all FLIP implementations
    - FLIP: Factory function for creating FLIP instances
"""

from flip.core.base import FLIPBase
from flip.core.factory import FLIP

__all__ = ["FLIPBase", "FLIP"]
