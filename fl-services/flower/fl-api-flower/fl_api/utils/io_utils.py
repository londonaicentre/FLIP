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

import json
from pathlib import Path


def read_config(config_json: Path) -> dict:
    """
    Reads a JSON configuration file and returns it as a dictionary.

    Args:
        config_json (Path): The path to the JSON configuration file.

    Returns:
        dict: The configuration as a dictionary.
    """
    with open(config_json, "r") as f:
        config = json.load(f)
    return config


def write_config(config: dict, config_json: Path):
    """
    Writes a dictionary to a JSON configuration file.

    Args:
        config (dict): The configuration as a dictionary.
        config_json (Path): The path to the JSON configuration file.
    """
    with open(config_json, "w") as f:
        json.dump(config, f, indent=2)
