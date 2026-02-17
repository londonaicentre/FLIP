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

"""Utility functions for FLIP."""

from uuid import UUID


class Utils:
    """Utility class with static helper methods."""

    @staticmethod
    def is_valid_uuid(val) -> bool:
        """
        Check if a value is a valid UUID.

        Args:
            val: Value to check (will be converted to string)

        Returns:
            bool: True if valid UUID, False otherwise
        """
        try:
            UUID(str(val))
            return True
        except ValueError:
            return False

    @staticmethod
    def is_string_empty(val: str) -> bool:
        """
        Check if a string is empty or contains only whitespace.

        Args:
            val: String to check

        Returns:
            bool: True if empty or whitespace-only, False otherwise
        """
        return val.strip() == ""


def convert_weights_to_diff(global_weights: dict, local_weights: dict) -> dict:
    """
    Convert model weights to weight differences.

    Args:
        global_weights (dict): The global model weights.
        local_weights (dict): The new model weights after local training.

    Returns:
        dict: The weight differences.
    """

    local_weights = {wn: w.detach().cpu() for wn, w in local_weights.items()}
    weight_diff = {}
    for name in local_weights:
        if name not in global_weights:
            print(f"Warning: weight {name} not found in global model weights.")
            continue
        diff_tensor = local_weights[name] - global_weights[name]
        weight_diff[name] = diff_tensor.numpy()
        return weight_diff
