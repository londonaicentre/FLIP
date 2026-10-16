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

"""A shipped meta.json is one the NVFLARE fl-api accepts (FLIP#70).

The fl-api checks a job's meta.json when training starts, after the researcher has uploaded the
files and queued the job. This catches a shipped tutorial's or template's mistake in CI instead.
It mirrors the fl-api's rule (fl-services/nvflare/fl-api-base/fl_api/utils/job_resources.py), whose
own tests are the authority; this suite runs in flip-utils' environment and cannot import it.
"""

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
# Written by FLIP from the run, or taken from the job; anything else is refused.
ACCEPTED_KEYS = {"name", "deploy_map", "min_clients", "mandatory_clients", "resource_spec", "custom_props"}
PORTABLE_KEYS = {"num_of_gpus", "num_of_cpus", "memory"}
MAX_GPUS_PER_SITE = 8


def _shipped_meta_files() -> list[Path]:
    roots = [REPO_ROOT / "fl-tutorials" / "nvflare", REPO_ROOT / "fl-apps" / "nvflare"]
    return [
        path for root in roots for path in sorted(root.rglob("meta.json")) if "data" not in path.relative_to(root).parts
    ]


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def test_the_arkplus_tutorials_declare_their_gpu_need():
    """They need a GPU; declaring it lets stag's hub-wide default go back to 0 (FLIP#1390)."""
    declared = {
        path.parts[-3]: json.loads(path.read_text(encoding="utf-8"))["resource_spec"]["@default"]["num_of_gpus"]
        for path in _shipped_meta_files()
        if path.parent.name == "app_files"
    }

    assert declared == {
        "arkplus_fine_tuning": 1,
        "arkplus_baseline_classification_evaluation": 1,
        "arkplus_multimodel_classification_evaluation": 1,
    }


@pytest.mark.parametrize("path", _shipped_meta_files(), ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_a_shipped_meta_json_has_the_shape_the_fl_api_accepts(path):
    meta = json.loads(path.read_text(encoding="utf-8"))

    assert isinstance(meta, dict)
    assert set(meta) <= ACCEPTED_KEYS, f"key(s) the fl-api refuses: {sorted(set(meta) - ACCEPTED_KEYS)}"
    spec = meta.get("resource_spec") or {}
    assert set(spec) <= {"@default"}, f"per-site block(s) the fl-api refuses: {sorted(set(spec) - {'@default'})}"
    block = spec.get("@default") or {}
    assert set(block) <= PORTABLE_KEYS, f"unknown field(s): {sorted(set(block) - PORTABLE_KEYS)}"
    if "num_of_gpus" in block:
        assert _is_int(block["num_of_gpus"]), "num_of_gpus must be an integer"
        assert 0 <= block["num_of_gpus"] <= MAX_GPUS_PER_SITE, f"num_of_gpus must be 0..{MAX_GPUS_PER_SITE}"
    if "num_of_cpus" in block:
        assert _is_int(block["num_of_cpus"]), "num_of_cpus must be an integer"
        assert block["num_of_cpus"] >= 1, "num_of_cpus must be at least 1"
    if "memory" in block:
        assert isinstance(block["memory"], str), "memory must be a string such as 16Gi"
        assert re.fullmatch(r"[1-9][0-9]*(Mi|Gi|Ti)", block["memory"]), "memory must be such as 16Gi"
