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

"""``make stage-kit`` must chown the staged kit to the uid of the backend the kit is
actually for — read from the kit's own shape, not from an ``FL_BACKEND`` nothing in the
chart Makefile derives (FLIP#1009 review). Before this, an operator following the
documented quickstart for a Flower trust got the kit chowned to NVFLARE's 1000 and a
SuperNode that could not read its own certificates.

The Makefile resolves the backend at parse time, scoped to the ``stage-kit`` goal, so
``make -n`` shows the real recipe — the chown it would run — without touching a
cluster, and an unrecognisable kit fails before the recipe is reached. These tests
drive exactly that.
"""

import os
import subprocess
from pathlib import Path

import pytest

CHART_DIR = Path(__file__).resolve().parents[1]

NVFLARE_UID = "1000"
FLOWER_UID = "49999"


def make_n_stage_kit(kit_src, *extra):
    """Run ``make -n stage-kit`` against ``kit_src``; return the CompletedProcess."""
    env = {k: v for k, v in os.environ.items() if k not in ("FL_BACKEND", "KIT_UID")}
    return subprocess.run(
        ["make", "-n", "stage-kit", f"KIT_SRC={kit_src}", "KUBE_CONTEXT=test", *extra],
        cwd=CHART_DIR,
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def nvflare_kit(tmp_path):
    kit = tmp_path / "Trust_1"
    for d in ("local", "startup", "transfer"):
        (kit / d).mkdir(parents=True)
    return kit


@pytest.fixture
def flower_kit(tmp_path):
    kit = tmp_path / "Trust_2"
    for d in ("certificates", "keys"):
        (kit / d).mkdir(parents=True)
    return kit


def test_nvflare_kit_is_chowned_to_the_nvflare_uid(nvflare_kit):
    result = make_n_stage_kit(nvflare_kit)
    assert result.returncode == 0, result.stderr
    assert f"chown -R {NVFLARE_UID}:{NVFLARE_UID}" in result.stdout
    assert "nvflare kit" in result.stdout


def test_flower_kit_is_chowned_to_the_supernode_uid_without_fl_backend(flower_kit):
    """The documented invocation names no backend; the kit's shape is enough."""
    result = make_n_stage_kit(flower_kit)
    assert result.returncode == 0, result.stderr
    assert f"chown -R {FLOWER_UID}:{FLOWER_UID}" in result.stdout
    assert f"chown -R {NVFLARE_UID}:" not in result.stdout
    assert "flower kit" in result.stdout


def test_keys_alone_mark_a_flower_kit(tmp_path):
    """A Flower kit is recognised by either of its directories, not only by both."""
    kit = tmp_path / "keys-only"
    (kit / "keys").mkdir(parents=True)
    result = make_n_stage_kit(kit)
    assert result.returncode == 0, result.stderr
    assert f"chown -R {FLOWER_UID}:{FLOWER_UID}" in result.stdout


def test_explicit_fl_backend_overrides_the_kit_shape(nvflare_kit):
    result = make_n_stage_kit(nvflare_kit, "FL_BACKEND=flower")
    assert result.returncode == 0, result.stderr
    assert f"chown -R {FLOWER_UID}:{FLOWER_UID}" in result.stdout


def test_explicit_kit_uid_overrides_everything(flower_kit):
    result = make_n_stage_kit(flower_kit, "KIT_UID=4242")
    assert result.returncode == 0, result.stderr
    assert "chown -R 4242:4242" in result.stdout


def test_a_kit_with_neither_shape_is_refused_by_name(tmp_path):
    kit = tmp_path / "not-a-kit"
    kit.mkdir()
    result = make_n_stage_kit(kit)
    assert result.returncode != 0
    assert str(kit) in result.stderr
    assert "cannot tell which backend" in result.stderr
    assert "chown" not in result.stdout


def test_a_kit_with_both_shapes_is_refused_by_name(tmp_path):
    kit = tmp_path / "two-kits-in-one"
    for d in ("startup", "keys"):
        (kit / d).mkdir(parents=True)
    result = make_n_stage_kit(kit)
    assert result.returncode != 0
    assert str(kit) in result.stderr
    assert "both an NVFLARE shape" in result.stderr
    assert "chown" not in result.stdout


def test_an_unknown_fl_backend_is_refused(nvflare_kit):
    result = make_n_stage_kit(nvflare_kit, "FL_BACKEND=bogus")
    assert result.returncode != 0
    assert "FL_BACKEND=bogus is not a backend" in result.stderr


def test_missing_kit_src_is_refused(tmp_path):
    result = make_n_stage_kit(tmp_path / "does-not-exist")
    assert result.returncode != 0
    assert "is not a directory" in result.stderr


def test_the_shape_check_is_scoped_to_stage_kit():
    """Other goals must not demand a KIT_SRC — the guard is for stage-kit alone."""
    env = {k: v for k, v in os.environ.items() if k not in ("FL_BACKEND", "KIT_UID", "KIT_SRC")}
    result = subprocess.run(["make", "-n", "help"], cwd=CHART_DIR, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
