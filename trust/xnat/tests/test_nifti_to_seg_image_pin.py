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
"""nifti_to_seg runs the image FLIP builds, at the version its Dockerfile declares (as export_mask's pin test)."""

import json
import re
import tomllib
from pathlib import Path

XNAT_DIR = Path(__file__).resolve().parents[1]
DOCKERFILE = XNAT_DIR / "nifti-to-seg" / "Dockerfile"
PYPROJECT = XNAT_DIR / "nifti-to-seg" / "pyproject.toml"
COMMAND = XNAT_DIR / "xnat" / "config" / "nifti_to_seg_command.json"


def _version() -> str:
    match = re.search(r"^ARG NIFTI_TO_SEG_VERSION=(\S+)$", DOCKERFILE.read_text(), re.MULTILINE)
    assert match, f"no `ARG NIFTI_TO_SEG_VERSION=` in {DOCKERFILE}"
    return match.group(1)


def test_the_command_runs_the_published_image_at_the_dockerfile_version() -> None:
    assert json.loads(COMMAND.read_text())["image"] == f"ghcr.io/londonaicentre/xnat-nifti-to-seg:{_version()}"


def test_the_project_metadata_carries_the_dockerfile_version() -> None:
    assert tomllib.loads(PYPROJECT.read_text())["project"]["version"] == _version()


def test_the_command_line_calls_the_script_the_image_ships() -> None:
    script = json.loads(COMMAND.read_text())["command-line"].split()[1]
    assert script == "/app/src/nifti_to_seg.py"
    assert (XNAT_DIR / "nifti-to-seg" / "src" / Path(script).name).is_file()


def test_the_wrapper_runs_on_a_resource_and_stores_nothing_itself() -> None:
    # The converter uploads the SEG through the ROI API; the Container Service writes nothing back.
    command = json.loads(COMMAND.read_text())
    (wrapper,) = command["xnat"]
    assert wrapper["contexts"] == ["xnat:resourceCatalog"]
    assert command["outputs"] == []
    assert wrapper["output-handlers"] == []
