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
documented quickstart for a Flower trust got the kit chowned to FLARE's 1000 and a
SuperNode that could not read its own certificates.

The Makefile resolves the backend at parse time, scoped to the ``stage-kit`` goal, so
``make -n`` shows the real recipe — the chown it would run — without touching a
cluster, and an unrecognisable kit fails before the recipe is reached. These tests
drive exactly that.
"""

import os
import re
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
    assert f"--owner {NVFLARE_UID}" in result.stdout
    assert "nvflare kit" in result.stdout


def test_node_names_the_node_and_none_leaves_the_choice_to_the_script(nvflare_kit):
    """NODE= picks the node on a multi-node cluster; the script refuses one without it (tests/test_stage_to_node.py)."""
    assert '--node "node-2"' in make_n_stage_kit(nvflare_kit, "NODE=node-2").stdout
    assert "--node" not in make_n_stage_kit(nvflare_kit).stdout


def test_flower_kit_is_chowned_to_the_supernode_uid_without_fl_backend(flower_kit):
    """The documented invocation names no backend; the kit's shape is enough."""
    result = make_n_stage_kit(flower_kit)
    assert result.returncode == 0, result.stderr
    assert f"--owner {FLOWER_UID}" in result.stdout
    assert f"--owner {NVFLARE_UID}" not in result.stdout
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
    only surfaces when FLARE writes the trained model into ``transfer/`` at the end of a run,
    or when site policy writes ``privacy.json`` into ``local/`` at container start. Neither
    ``aws s3 sync`` nor ``docker cp`` recreates an empty directory, so a sync-assembled kit
    arrives without them. The mkdir has to precede the chown or the created directories keep
    root's ownership, which is the bug it exists to prevent (FLIP#1958 moved the steps into
    scripts/stage-to-node.sh; tests/test_stage_to_node.py pins their order).
    """
    kit = request.getfixturevalue(fixture_name)
    result = make_n_stage_kit(kit)
    assert result.returncode == 0, result.stderr

    subdirs = re.search(r'--subdirs "([^"]*)"', result.stdout)
    assert subdirs, f"no --subdirs passed to the staging script: {result.stdout}"
    assert subdirs.group(1).split() == list(expected)
    for name in not_expected:
        assert name not in subdirs.group(1).split(), f"{name} belongs to the other backend"
    # That the script creates them before the chown, on both transports: tests/test_stage_to_node.py.


def test_the_destination_is_emptied_before_the_copy(nvflare_kit):
    """A copy merges into the destination, so a re-stage must clear it first.

    Without the wipe, re-staging after a kit-date bump, a backend switch, or a corrected
    over-broad Flower sync leaves the previous kit's files beside the new ones — including
    credentials that should no longer be on the node. That is the same wipe-then-fetch
    property the EC2 play establishes for the Ansible path (FLIP#1009 review). The wipe has
    to precede the copy, or it deletes what was just staged.
    """
    result = make_n_stage_kit(nvflare_kit)
    assert result.returncode == 0, result.stderr

    # The script wipes --dest before it copies, on both transports (tests/test_stage_to_node.py); this
    # pins that KIT_DEST is what it is handed.
    assert '--dest "/opt/flip/fl-kit"' in result.stdout
    assert "stage-to-node.sh" in result.stdout


@pytest.mark.parametrize("bad_dest", ["", "/"])
def test_a_destination_the_wipe_must_not_touch_is_refused(nvflare_kit, bad_dest):
    """The wipe is the one destructive step here, so its two escape values fail at parse time.

    An empty KIT_DEST would make it ``find  -mindepth 1 -delete``, walking the working
    directory; ``/`` would walk the node's root filesystem.
    """
    result = make_n_stage_kit(nvflare_kit, f"KIT_DEST={bad_dest}")
    assert result.returncode != 0
    assert "KIT_DEST must not be" in result.stderr
    assert "stage-to-node.sh" not in result.stdout


def test_keys_alone_mark_a_flower_kit(tmp_path):
    """A Flower kit is recognised by either of its directories, not only by both."""
    kit = tmp_path / "keys-only"
    (kit / "keys").mkdir(parents=True)
    result = make_n_stage_kit(kit)
    assert result.returncode == 0, result.stderr
    assert f"--owner {FLOWER_UID}" in result.stdout


def test_explicit_fl_backend_overrides_the_kit_shape(nvflare_kit):
    result = make_n_stage_kit(nvflare_kit, "FL_BACKEND=flower")
    assert result.returncode == 0, result.stderr
    assert f"--owner {FLOWER_UID}" in result.stdout


def test_explicit_kit_uid_overrides_everything(flower_kit):
    result = make_n_stage_kit(flower_kit, "KIT_UID=4242")
    assert result.returncode == 0, result.stderr
    assert "--owner 4242" in result.stdout


def test_a_kit_with_neither_shape_is_refused_by_name(tmp_path):
    kit = tmp_path / "not-a-kit"
    kit.mkdir()
    result = make_n_stage_kit(kit)
    assert result.returncode != 0
    assert str(kit) in result.stderr
    assert "cannot tell which backend" in result.stderr
    assert "stage-to-node.sh" not in result.stdout


def test_a_kit_with_both_shapes_is_refused_by_name(tmp_path):
    kit = tmp_path / "two-kits-in-one"
    for d in ("startup", "keys"):
        (kit / d).mkdir(parents=True)
    result = make_n_stage_kit(kit)
    assert result.returncode != 0
    assert str(kit) in result.stderr
    assert "both a FLARE shape" in result.stderr
    assert "stage-to-node.sh" not in result.stdout


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


def _kit_file(tmp_path, *lines):
    kit_file = tmp_path / ".env.ABC.prod"
    kit_file.write_text("\n".join(("FL_BACKEND=nvflare", *lines)) + "\n")
    return kit_file


def test_the_kit_goes_under_the_kits_data_root(nvflare_kit, tmp_path):
    """A kit with FLIP_DATA_ROOT (FLIP#2113) stages to fl-kit/ under it, where a deploy with that KIT mounts."""
    kit_file = _kit_file(tmp_path, "FLIP_DATA_ROOT=/data/flip/ABC-prod/")
    result = make_n_stage_kit(nvflare_kit, "KIT=ABC", f"KIT_FILE={kit_file}")
    assert result.returncode == 0, result.stderr
    assert '--dest "/data/flip/ABC-prod/fl-kit"' in result.stdout


def test_kit_dest_beats_the_data_root(nvflare_kit, tmp_path):
    kit_file = _kit_file(tmp_path, "FLIP_DATA_ROOT=/data/flip/ABC-prod")
    result = make_n_stage_kit(nvflare_kit, "KIT=ABC", f"KIT_FILE={kit_file}", "KIT_DEST=/srv/kit")
    assert result.returncode == 0, result.stderr
    assert '--dest "/srv/kit"' in result.stdout


def test_a_relative_data_root_is_refused(nvflare_kit, tmp_path):
    kit_file = _kit_file(tmp_path, "FLIP_DATA_ROOT=data/flip")
    result = make_n_stage_kit(nvflare_kit, "KIT=ABC", f"KIT_FILE={kit_file}")
    assert result.returncode != 0
    assert "FLIP_DATA_ROOT must be an absolute path" in result.stderr


def test_an_inline_comment_on_the_data_root_is_dropped(nvflare_kit, tmp_path):
    """As make reads an included kit: the comment must not end up in the path."""
    kit_file = _kit_file(tmp_path, "FLIP_DATA_ROOT=/data/flip/ABC-prod  # data disk")
    result = make_n_stage_kit(nvflare_kit, "KIT=ABC", f"KIT_FILE={kit_file}")
    assert result.returncode == 0, result.stderr
    assert '--dest "/data/flip/ABC-prod/fl-kit"' in result.stdout


@pytest.mark.parametrize(
    ("line", "error"),
    [("FLIP_DATA_ROOT=/", "not / itself"), ("FLIP_DATA_ROOT=/mnt/Data Disk/flip", "one path with no spaces")],
)
def test_a_malformed_data_root_is_refused(nvflare_kit, tmp_path, line, error):
    result = make_n_stage_kit(nvflare_kit, "KIT=ABC", f"KIT_FILE={_kit_file(tmp_path, line)}")
    assert result.returncode != 0
    assert error in result.stderr


def test_a_kit_that_names_no_kit_file_is_refused(nvflare_kit, tmp_path):
    """Staging to /opt/flip while the deploy with that KIT mounts the root's fl-kit/ leaves the pod Pending."""
    result = make_n_stage_kit(nvflare_kit, "KIT=ABC", f"KIT_FILE={tmp_path / '.env.ABC.prod'}")
    assert result.returncode != 0
    assert "names no kit file" in result.stderr


def test_an_exported_data_root_is_not_the_kits(nvflare_kit, tmp_path, monkeypatch):
    monkeypatch.setenv("FLIP_DATA_ROOT", "/data/other-trust")
    result = make_n_stage_kit(nvflare_kit, "KIT=ABC", f"KIT_FILE={_kit_file(tmp_path)}")
    assert result.returncode == 0, result.stderr
    assert '--dest "/opt/flip/fl-kit"' in result.stdout
