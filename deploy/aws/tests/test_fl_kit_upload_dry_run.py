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

"""Both backends' kit upload targets default to a dry run (FLIP#2047).

Each is an ``aws s3 sync --delete`` over a whole net's prefix, so run against a live kit date it
removes every key ``add-fl-kits`` put there. The FLARE target always previewed unless given
``DRYRUN=``; the Flower one used ``$(DRYRUN)`` without defining it and uploaded for real.

The last test covers the Flower Makefile's other guard, on ``up-secure``.
"""

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
#: The caller's shell must not choose the mode under test, nor the developer's own env file
#: (which both Makefiles include): the probe names one that does not exist.
ENV = {k: v for k, v in os.environ.items() if k not in {"DRYRUN", "HUB", "MAKEFLAGS", "MAIN_ENV_FILE"}}
NO_ENV_FILE = "MAIN_ENV_FILE=.env.fl-kit-upload-probe"

UPLOADS = [("flower", "upload-creds-to-s3", "FLOWER_KIT_DATE"), ("nvflare", "upload-kits-to-s3", "FLARE_KIT_DATE")]


def _syncs(backend: str, target: str, kit_date_var: str, *extra: str) -> list[str]:
    result = subprocess.run(
        ["make", "-n", "-C", str(REPO_ROOT / "fl-services" / backend), target]
        + [NO_ENV_FILE, "AICENTRE_BUCKET_NAME=probe-bucket", f"{kit_date_var}=20260101", *extra],
        env=ENV,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    syncs = [line.strip() for line in result.stdout.splitlines() if line.startswith("aws s3 sync")]
    assert syncs, result.stdout
    return syncs


@pytest.mark.parametrize(("backend", "target", "kit_date_var"), UPLOADS)
def test_the_upload_is_a_dry_run_unless_asked_otherwise(backend: str, target: str, kit_date_var: str) -> None:
    for sync in _syncs(backend, target, kit_date_var):
        assert "--delete" in sync
        assert sync.endswith(" --dryrun"), sync


@pytest.mark.parametrize(("backend", "target", "kit_date_var"), UPLOADS)
def test_an_empty_dryrun_uploads_for_real(backend: str, target: str, kit_date_var: str) -> None:
    for sync in _syncs(backend, target, kit_date_var, "DRYRUN="):
        assert "--dryrun" not in sync


def test_the_secure_harness_refuses_to_start_without_the_fl_api_key() -> None:
    """Its SuperNodes wait on the key-registration one-shot, which an FL API with no key answers 401."""
    result = subprocess.run(
        ["make", "-C", str(REPO_ROOT / "fl-services" / "flower"), "-o", "_ensure-dev-dirs", "up-secure"]
        + [NO_ENV_FILE, "FL_API_KEY="],
        env=ENV,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "FL_API_KEY is not set" in result.stdout + result.stderr
    assert "docker" not in result.stdout + result.stderr
