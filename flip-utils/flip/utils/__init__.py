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
FLIP Utilities module containing helper functions.

Exports:
    - Utils: Utility class with static helper methods
    - get_model_weights_diff: Compute weight differences for federated updates
"""

from flip.utils.model_weights_handling import get_model_weights_diff
from flip.utils.utils import Utils

__all__ = ["Utils", "get_model_weights_diff"]
