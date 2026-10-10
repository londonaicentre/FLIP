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

"""A tutorial's config.json RESOURCE_SPEC is one the NVFLARE fl-api accepts (FLIP#70).

The fl-api refuses a malformed RESOURCE_SPEC when training starts, after the researcher has
uploaded the files and queued the job. This catches a shipped tutorial's mistake in CI instead.
It mirrors the fl-api's rule (fl-services/nvflare/fl-api-base/fl_api/utils/job_resources.py), whose
own tests are the authority; this suite runs in flip-utils' environment and cannot import it.
"""

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
NVFLARE_KEYS = {"num_of_gpus", "mem_per_gpu_in_GiB"}


def _configs_declaring_resources() -> list[Path]:
    roots = [REPO_ROOT / "fl-tutorials" / "nvflare", REPO_ROOT / "fl-apps" / "nvflare"]
    found = []
    for root in roots:
        for path in sorted(root.rglob("config.json")):
            if "data" in path.relative_to(root).parts:
                continue
            if "RESOURCE_SPEC" in json.loads(path.read_text(encoding="utf-8")):
                found.append(path)
    return found


def _is_count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def test_the_arkplus_tutorials_declare_their_gpu_need():
    """They need a GPU with 7 GiB free; declaring it lets stag's hub-wide default go back to 0 (FLIP#1390)."""
    names = {path.parts[-3] for path in _configs_declaring_resources()}

    assert {
        "arkplus_fine_tuning",
        "arkplus_baseline_classification_evaluation",
        "arkplus_multimodel_classification_evaluation",
    } <= names


@pytest.mark.parametrize("path", _configs_declaring_resources(), ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_a_declared_resource_spec_has_the_shape_the_fl_api_accepts(path):
    spec = json.loads(path.read_text(encoding="utf-8"))["RESOURCE_SPEC"]

    assert isinstance(spec, dict)
    assert set(spec) <= NVFLARE_KEYS, f"unknown key(s): {sorted(set(spec) - NVFLARE_KEYS)}"
    assert _is_count(spec.get("num_of_gpus")), "num_of_gpus must be a non-negative integer"
    mem = spec.get("mem_per_gpu_in_GiB", 0)
    assert _is_count(mem), "mem_per_gpu_in_GiB must be a non-negative integer"
    assert not (mem and not spec["num_of_gpus"]), "mem_per_gpu_in_GiB needs num_of_gpus > 0"
