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

"""``make download-vocab`` and ``make stage-vocab`` (FLIP#923), driven with ``make -n``.

download-vocab runs on the operator's host with their own AWS CLI profile; stage-vocab puts the
folder where the chart's omopDb.vocabLoad.hostPath mounts it. Nothing touches a cluster or AWS.
"""

import subprocess
from pathlib import Path

import pytest

CHART_DIR = Path(__file__).resolve().parents[1]
DEST = "/opt/flip/omop-vocab"


def make_n(target: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["make", "-n", target, *args], cwd=CHART_DIR, capture_output=True, text=True, timeout=60)


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    (tmp_path / "CONCEPT.csv").write_text("concept_id\n")
    return tmp_path


def test_stage_vocab_copies_the_folder_where_the_chart_mounts_it(bundle):
    result = make_n("stage-vocab", f"VOCAB_DIR={bundle}", "KUBE_CONTEXT=test")
    assert result.returncode == 0, result.stderr
    assert f'docker cp "{bundle}/." "$node:{DEST}/"' in result.stdout
    assert f'chmod -R a+rX "{DEST}"' in result.stdout
    assert f"omopDb.vocabLoad.hostPath={DEST}" in result.stdout


@pytest.mark.parametrize("vocab_dir", ["", "/nonexistent"])
def test_stage_vocab_refuses_a_folder_without_the_bundle(vocab_dir):
    result = make_n("stage-vocab", f"VOCAB_DIR={vocab_dir}", "KUBE_CONTEXT=test")
    assert result.returncode != 0
    assert "VOCAB_DIR" in result.stderr


def test_download_vocab_runs_the_host_side_fetch():
    result = make_n("download-vocab", "VOCAB_S3_BUCKET=some-bucket")
    assert result.returncode == 0, result.stderr
    want = "fetch-vocab-core VOCAB_CORE_BUNDLE=vocab_aicentre_core_20240916 VOCAB_S3_BUCKET=some-bucket"
    assert want in result.stdout
    assert "stage-vocab VOCAB_DIR=" in result.stdout
