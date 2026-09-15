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


@pytest.mark.parametrize(
    ("fixture_name", "expected", "not_expected"),
    [
        ("nvflare_kit", ("local", "startup", "transfer"), ("certificates", "keys")),
        ("flower_kit", ("certificates", "keys"), ("local", "startup", "transfer")),
    ],
)
def test_mounted_subdirectories_are_created_before_the_chown(request, fixture_name, expected, not_expected):
    """Every subPath the chart mounts must exist under KIT_DEST before the pod starts.

    A subPath missing from the hostPath is created by kubelet as root:root 0700 and the
    non-root fl-client cannot write it, while the pod still reports Running — so the failure
    only surfaces when NVFLARE writes the trained model into ``transfer/`` at the end of a run,
    or when site policy writes ``privacy.json`` into ``local/`` at container start. Neither
    ``aws s3 sync`` nor ``docker cp`` recreates an empty directory, so a sync-assembled kit
    arrives without them. The mkdir has to precede the chown or the created directories keep
    root's ownership, which is the bug it exists to prevent.
    """
    kit = request.getfixturevalue(fixture_name)
    result = make_n_stage_kit(kit)
    assert result.returncode == 0, result.stderr

    mkdir_lines = [ln for ln in result.stdout.splitlines() if "mkdir -p" in ln and "fl-kit" in ln]
    subdir_line = next((ln for ln in mkdir_lines if any(f"/{d}" in ln for d in expected)), None)
    assert subdir_line is not None, f"no mkdir for the mounted subdirectories: {mkdir_lines}"
    for name in expected:
        assert f"/{name}" in subdir_line, f"{name} not pre-created: {subdir_line}"
    for name in not_expected:
        assert f"/{name}" not in subdir_line, f"{name} belongs to the other backend: {subdir_line}"

    assert result.stdout.index(subdir_line) < result.stdout.index("chown -R"), (
        "the subdirectory mkdir must run before the chown, or the new dirs stay root-owned"
    )


def test_the_destination_is_emptied_before_the_copy(nvflare_kit):
    """``docker cp`` merges into the destination, so a re-stage must clear it first.

    Without the wipe, re-staging after a kit-date bump, a backend switch, or a corrected
    over-broad Flower sync leaves the previous kit's files beside the new ones — including
    credentials that should no longer be on the node. That is the same wipe-then-fetch
    property the EC2 play establishes for the Ansible path (FLIP#1009 review). The wipe has
    to precede the copy, or it deletes what was just staged.
    """
    result = make_n_stage_kit(nvflare_kit)
    assert result.returncode == 0, result.stderr

    # `make -n` echoes the recipe's own `##` doc comments, which mention both commands by
    # name — so compare positions among the command lines only.
    commands = [ln for ln in result.stdout.splitlines() if not ln.lstrip().startswith("##")]

    wipe = next((i for i, ln in enumerate(commands) if "-mindepth 1 -delete" in ln), None)
    assert wipe is not None, f"destination is never cleared: {result.stdout}"
    assert "/opt/flip/fl-kit" in commands[wipe], f"the wipe does not name KIT_DEST: {commands[wipe]}"

    copy = next(i for i, ln in enumerate(commands) if "docker cp" in ln)
    assert wipe < copy, "the wipe must run before the copy, or it removes the kit it just staged"


@pytest.mark.parametrize("bad_dest", ["", "/"])
def test_a_destination_the_wipe_must_not_touch_is_refused(nvflare_kit, bad_dest):
    """The wipe is the one destructive step here, so its two escape values fail at parse time.

    An empty KIT_DEST would make it ``find  -mindepth 1 -delete``, walking the working
    directory; ``/`` would walk the node's root filesystem.
    """
    result = make_n_stage_kit(nvflare_kit, f"KIT_DEST={bad_dest}")
    assert result.returncode != 0
    assert "KIT_DEST must not be" in result.stderr
    assert "docker cp" not in result.stdout


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
