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


def to_pascal_case(snake_str: str) -> str:
    """
    Convert a snake_case string to PascalCase.

    This is needed for the UI to understand the permissions format.
    Example: CAN_ACCESS_ADMIN_PANEL -> CanAccessAdminPanel

    Args:
        snake_str (str): The snake_case (or UPPER_SNAKE_CASE) string to convert.

    Returns:
        str: The input string converted to PascalCase.
    """
    return "".join(word.capitalize() for word in snake_str.lower().split("_"))
