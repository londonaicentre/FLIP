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
FLIP Executors module containing NVFLARE executor wrappers.

These executors wrap user-provided training and validation logic.

Exports:
    - RUN_TRAINER: Wrapper executor for user's FLIP_TRAINER class
    - RUN_VALIDATOR: Wrapper executor for user's FLIP_VALIDATOR class
"""

from flip.executors.trainer import RUN_TRAINER
from flip.executors.validator import RUN_VALIDATOR

__all__ = ["RUN_TRAINER", "RUN_VALIDATOR"]
