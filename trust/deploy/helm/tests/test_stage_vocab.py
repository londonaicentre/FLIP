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
folder where the chart's omopDb.vocabLoad.hostPath mounts it. ``make -n`` prints recipes but runs
``$(MAKE)`` lines, so download-vocab's dry run also parses trust/omop-db's Makefile; nothing here
touches a cluster or AWS. Skipped without make.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

CHART_DIR = Path(__file__).resolve().parents[1]
DEST = "/opt/flip/omop-vocab"

pytestmark = pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")


def make_n(target: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["make", "-n", target, *args], cwd=CHART_DIR, capture_output=True, text=True, timeout=60)


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    (tmp_path / "CONCEPT.csv").write_text("concept_id\n")
    return tmp_path


def test_stage_vocab_copies_the_folder_where_the_chart_mounts_it(bundle):
    result = make_n("stage-vocab", f"VOCAB_DIR={bundle}", "KUBE_CONTEXT=test")
    assert result.returncode == 0, result.stderr
    # Emptied first, so a narrower re-stage leaves no table file of the previous bundle behind.
    assert f'find "{DEST}" -mindepth 1 -delete' in result.stdout
    assert result.stdout.index("-mindepth 1 -delete") < result.stdout.index('docker cp "')
    assert f'docker cp "{bundle}/." "$node:{DEST}/"' in result.stdout
    assert f'chmod -R a+rX "{DEST}"' in result.stdout
    assert f"omopDb.vocabLoad.hostPath={DEST}" in result.stdout


@pytest.mark.parametrize("vocab_dir", ["", "/nonexistent", "EMPTY"])
def test_stage_vocab_refuses_a_folder_without_the_bundle(vocab_dir, tmp_path):
    # EMPTY: an existing folder with no CONCEPT.csv (the still-zipped bundle, or its parent).
    if vocab_dir == "EMPTY":
        (tmp_path / "bundle.zip").write_text("")
        vocab_dir = str(tmp_path)
    result = make_n("stage-vocab", f"VOCAB_DIR={vocab_dir}", "KUBE_CONTEXT=test")
    assert result.returncode != 0
    assert "VOCAB_DIR" in result.stderr


@pytest.mark.parametrize("dest", ["", "/"])
def test_stage_vocab_refuses_a_destination_the_wipe_would_overreach(bundle, dest):
    result = make_n("stage-vocab", f"VOCAB_DIR={bundle}", f"VOCAB_DEST={dest}", "KUBE_CONTEXT=test")
    assert result.returncode != 0
    assert "VOCAB_DEST must not be" in result.stderr


def test_download_vocab_runs_the_host_side_fetch():
    result = make_n("download-vocab", "VOCAB_S3_BUCKET=some-bucket")
    assert result.returncode == 0, result.stderr
    want = "fetch-vocab-core VOCAB_CORE_BUNDLE=vocab_aicentre_core_20240916 VOCAB_S3_BUCKET=some-bucket"
    assert want in result.stdout
    assert "stage-vocab VOCAB_DIR=" in result.stdout
