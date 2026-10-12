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

"""The spleen label uploader's repo-relative paths resolve from where the file actually lives.

Pins FLIP#1102: a ``parents[N]`` index is a depth assumption, and moving the file (d6ce346b) broke
it silently — nothing between the move and a live enrichment run reads the path.
"""

import importlib.util
from pathlib import Path

UPLOADER = Path(__file__).resolve().parents[1] / "datasets" / "spleen" / "upload_spleen_labels_to_xnat.py"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_uploader():
    spec = importlib.util.spec_from_file_location("upload_spleen_labels_to_xnat_under_test", UPLOADER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_omop_data_version_file_is_the_repos_pin():
    uploader = _load_uploader()

    assert uploader.OMOP_DATA_VERSION_FILE == REPO_ROOT / "trust" / ".data_version"
    assert uploader.OMOP_DATA_VERSION_FILE.is_file(), (
        "the parents[N] index in OMOP_DATA_VERSION_FILE no longer matches this file's depth"
    )


def test_omop_data_version_reads_the_pinned_value():
    uploader = _load_uploader()

    assert uploader.omop_data_version() == (REPO_ROOT / "trust" / ".data_version").read_text().strip()
