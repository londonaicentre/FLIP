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
"""export_mask runs the image FLIP builds, at the version its Dockerfile declares.

XNAT matches the registered command by its exact image string, and the publish workflow pushes
``ghcr.io/londonaicentre/xnat-export-mask:<EXPORT_MASK_VERSION>``. A version bump that leaves the
command JSON behind keeps registering the old image — silently, since that tag still exists.
"""

import json
import re
import tomllib
from pathlib import Path

XNAT_DIR = Path(__file__).resolve().parents[1]
DOCKERFILE = XNAT_DIR / "export-mask" / "Dockerfile"
PYPROJECT = XNAT_DIR / "export-mask" / "pyproject.toml"
COMMAND = XNAT_DIR / "xnat" / "config" / "export_mask_command.json"


def _version() -> str:
    match = re.search(r"^ARG EXPORT_MASK_VERSION=(\S+)$", DOCKERFILE.read_text(), re.MULTILINE)
    assert match, f"no `ARG EXPORT_MASK_VERSION=` in {DOCKERFILE}"
    return match.group(1)


def test_the_command_runs_the_published_image_at_the_dockerfile_version() -> None:
    image = json.loads(COMMAND.read_text())["image"]
    assert image == f"ghcr.io/londonaicentre/xnat-export-mask:{_version()}"


def test_the_project_metadata_carries_the_dockerfile_version() -> None:
    # uv requires [project].version even with package = false, so it cannot simply be dropped.
    assert tomllib.loads(PYPROJECT.read_text())["project"]["version"] == _version()


def test_the_command_line_calls_the_script_the_image_ships() -> None:
    command_line = json.loads(COMMAND.read_text())["command-line"]
    script = command_line.split()[1]
    assert script == "/app/src/export_mask.py"
    assert (XNAT_DIR / "export-mask" / "src" / Path(script).name).is_file()
