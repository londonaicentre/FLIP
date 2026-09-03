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
"""
Tests for XNAT bind-mount ownership (FLIP#1095).

``xnat-web`` runs as the in-image ``xnat`` user, whose id is fixed in
``trust/xnat/xnat/Dockerfile``. Every host directory bind-mounted into it must be
owned by that id. When one is not, XNAT accepts the inbound DICOM association,
fails to write the object, and aborts — so the PACS reports a *network* fault
("Peer aborted Association") and the same permission failure stops XNAT writing
the application logs that would name the real cause.

Three provisioning paths must therefore agree on one number: ``xnat-reset`` in
``trust/xnat/Makefile``, and the two Ansible playbooks. They previously did not —
``xnat-reset`` used a hardcoded 1000 on its remote branch and the invoking user's
id on the others.

The static tests pin that agreement. They cannot see whether the guard still
*runs*, though: a branch that drops the check, or a check that can no longer fail,
leaves every literal untouched. ``TestOwnershipGuardFires`` closes that half by
extracting the shell the recipe will actually execute (via ``make -n``) and running
it against real directories. It needs neither root nor Docker: rather than
chown-ing to a foreign id, it overrides ``XNAT_CONTAINER_UID``/``GID`` on the make
command line, which is what the recipe compares against.

That override is also its blind spot, and ``TestDevBranchProvisionsTheContainerUid``
exists because of it. Expecting the invoker's own id, and running only the extracted
``owned=`` line, together remove the one condition that matters — provisioning for a
uid the caller is not. Those tests therefore pass whether or not the recipe can
actually reach the ownership it demands. The second class runs the whole generated
development branch (wipe, mkdir, chown, check) with a ``sudo`` shim that drops the
privilege, so an unreachable owner fails there as it would on a developer's machine.
Note that this host and the GitHub runner are both uid 1001, the same id the XNAT
image uses, so any test written against that literal is vacuous in both places it
runs; the uid is always derived from ``os.getuid()``.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

XNAT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = XNAT_DIR.parents[1]
MAKEFILE = XNAT_DIR / "Makefile"
DOCKERFILE = XNAT_DIR / "xnat" / "Dockerfile"
AWS_PLAYBOOK = REPO_ROOT / "deploy" / "providers" / "AWS" / "site.yml"
LOCAL_PLAYBOOK = REPO_ROOT / "deploy" / "providers" / "local" / "site_local_trust.yml"

# Long enough for a `make -n` parse; short enough that a hang fails the suite
# rather than wedging CI.
TIMEOUT_SECONDS = 60


def _make_var(name: str) -> str:
    """Return the literal a `NAME := value` assignment in trust/xnat/Makefile binds."""
    match = re.search(rf"^{re.escape(name)}\s*:?=\s*(\S+)\s*$", MAKEFILE.read_text(), re.MULTILINE)
    assert match, f"{name} is not assigned in {MAKEFILE}"
    return match.group(1)


def _reset_recipe() -> str:
    """Return the body of the `xnat-reset` recipe."""
    text = MAKEFILE.read_text()
    start = text.index("\nxnat-reset:")
    # A recipe ends at the first line that is neither blank, a comment, nor indented.
    body: list[str] = []
    for line in text[start + 1 :].splitlines()[1:]:
        if line and not line.startswith(("\t", " ", "#")):
            break
        body.append(line)
    return "\n".join(body)


class TestUidIsConsistent:
    """The Makefile, the Dockerfile and both playbooks must name the same uid."""

    def test_dockerfile_uid_matches_makefile(self) -> None:
        match = re.search(r"useradd\b[^\n]*--uid\s+(\d+)[^\n]*\bxnat\b", DOCKERFILE.read_text())
        assert match, "no `useradd --uid <N> … xnat` in the XNAT Dockerfile"
        assert _make_var("XNAT_CONTAINER_UID") == match.group(1), (
            "XNAT_CONTAINER_UID in trust/xnat/Makefile has drifted from the uid the "
            "XNAT image actually creates. The Makefile provisions the bind mounts that "
            "the image writes to, so a mismatch makes them unwritable."
        )

    def test_gid_matches_uid(self) -> None:
        # The Dockerfile creates the group and the user with the same id.
        assert _make_var("XNAT_CONTAINER_GID") == _make_var("XNAT_CONTAINER_UID")

    @pytest.mark.parametrize("playbook", [AWS_PLAYBOOK, LOCAL_PLAYBOOK], ids=["aws", "on-prem"])
    def test_playbooks_provision_the_same_uid(self, playbook: Path) -> None:
        uid = _make_var("XNAT_CONTAINER_UID")
        text = playbook.read_text()
        # Anchored on the task's own indent and ended by the next sibling at that indent (or EOF),
        # so reindenting the play, reordering it, or making this the last task in the file does not
        # turn a real drift check into "no XNAT bind-mount directory task" — a failure that accuses
        # the playbook when the fault is in this regex.
        task = re.search(
            r"^(?P<indent>[ \t]*)- name:.*XNAT bind-mount directories.*?(?=^(?P=indent)- |\Z)",
            text,
            re.DOTALL | re.MULTILINE,
        )
        assert task, f"no XNAT bind-mount directory task in {playbook}"
        assert re.search(rf'owner:\s*"{uid}"', task.group(0)), (
            f"{playbook.name} provisions the XNAT bind mounts as a different uid than "
            f"trust/xnat/Makefile ({uid}). All three provisioning paths must agree."
        )


class TestResetRecipeDerivesTheOwner:
    """`xnat-reset` must not reintroduce a hardcoded or host-derived owner."""

    def test_no_hardcoded_foreign_uid(self) -> None:
        uid = _make_var("XNAT_CONTAINER_UID")
        stray = [n for n in re.findall(r"chown\s+(?:-R\s+)?(\d+):\d+", _reset_recipe()) if n != uid]
        assert not stray, (
            f"xnat-reset chowns the XNAT bind mounts to {stray}, but xnat-web runs as {uid}. "
            "This is the FLIP#1095 regression: use $(XNAT_DIR_OWNER)."
        )

    def test_owner_is_not_derived_from_the_invoking_user(self) -> None:
        assert "$(USER)" not in _reset_recipe(), (
            "xnat-reset derives the bind-mount owner from the invoking host user. The uid "
            "is a property of the XNAT image, not of whoever ran make, so this only works "
            "on a host whose login account happens to share it."
        )

    def test_every_branch_verifies_ownership(self) -> None:
        # remote-swarm, local-prod and development: each provisions dirs, so each must check.
        assert _reset_recipe().count("check_xnat_dir_owner") == 3, (
            "one of xnat-reset's three provisioning branches no longer verifies ownership. "
            "An unchecked branch fails silently and the symptom surfaces in the PACS."
        )


class TestOwnershipGuardFires:
    """Execute the generated guard: it must accept a correct tree and reject a broken one."""

    @staticmethod
    def _guard(data_dir: Path, expected_uid: int, expected_gid: int) -> str:
        """Extract the shell the *local* branches of xnat-reset run to verify ownership."""
        result = subprocess.run(
            [
                "make", "-n", "--no-print-directory", "xnat-reset",
                "KIT=GSTT",
                f"XNAT_DATA_DIR={data_dir}",
                f"XNAT_CONTAINER_UID={expected_uid}",
                f"XNAT_CONTAINER_GID={expected_gid}",
            ],
            cwd=XNAT_DIR,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
        assert result.returncode == 0, f"make -n xnat-reset failed:\n{result.stderr}"
        # Both local branches emit the same check; the remote one wraps it in ssh.
        local = [ln for ln in result.stdout.splitlines() if "owned=" in ln and "ssh " not in ln]
        assert local, "xnat-reset emitted no local ownership check"
        # Recipe lines carry make's trailing continuation; `sh` would read a lone
        # backslash as a command and exit 127 before the guard ever ran.
        return local[-1].rstrip().removesuffix("\\")

    @staticmethod
    def _make_tree(root: Path) -> Path:
        data = root / "data"
        for name in ("tomcat_logs", "archive", "build", "cache"):
            (data / "xnat-data" / name).mkdir(parents=True)
        return data

    def test_accepts_a_correctly_owned_tree(self, tmp_path: Path) -> None:
        data = self._make_tree(tmp_path)
        guard = self._guard(data, os.getuid(), os.getgid())
        assert subprocess.run(["sh", "-c", guard], timeout=TIMEOUT_SECONDS).returncode == 0

    def test_rejects_a_wrongly_owned_tree(self, tmp_path: Path) -> None:
        """The FLIP#1095 failure: dirs exist but belong to someone the container is not."""
        data = self._make_tree(tmp_path)
        # Expect an id the tree demonstrably is not, without needing root to chown.
        guard = self._guard(data, os.getuid() + 1, os.getgid() + 1)
        result = subprocess.run(["sh", "-c", guard], capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
        assert result.returncode == 1
        assert "Fix: sudo chown -R" in result.stdout, "the guard must name the remedy, not just fail"

    def test_rejects_a_missing_directory(self, tmp_path: Path) -> None:
        """A dir absent entirely must fail too — `stat` prints nothing rather than erroring out."""
        data = self._make_tree(tmp_path)
        (data / "xnat-data" / "cache").rmdir()
        guard = self._guard(data, os.getuid(), os.getgid())
        assert subprocess.run(["sh", "-c", guard], capture_output=True, timeout=TIMEOUT_SECONDS).returncode == 1


class TestDevBranchProvisionsTheContainerUid:
    """Execute the development branch end to end: mkdir, chown and check together.

    ``TestOwnershipGuardFires`` runs only the extracted ``owned=`` line and overrides the expected
    uid to the invoker's, so between them those two choices remove exactly the condition this issue
    is about — provisioning directories for a uid that is *not* the caller's. That is why the suite
    stayed green while the development branch could not reach the ownership it demands.

    These run the whole generated sequence instead. Neither needs root: a ``sudo`` shim on PATH
    ``exec``s its arguments unprivileged, which is precisely the constraint a normal developer runs
    under, so a chown to a foreign id fails here exactly as it would on their machine.

    The uid is derived as ``os.getuid() + 1`` rather than written as a literal. This host and the
    GitHub runner both happen to be uid 1001 — the same id the XNAT image uses — so a literal would
    make these vacuous in exactly the two places they run.
    """

    @staticmethod
    def _sudo_shim(root: Path) -> Path:
        """A PATH entry whose `sudo` runs the command unprivileged, modelling a non-root developer."""
        bin_dir = root / "shim"
        bin_dir.mkdir()
        shim = bin_dir / "sudo"
        shim.write_text('#!/bin/sh\nexec "$@"\n')
        shim.chmod(0o755)
        return bin_dir

    @staticmethod
    def _dev_recipe(data_dir: Path, expected_uid: int, expected_gid: int) -> str:
        """The full shell the development branch of xnat-reset runs (PROD unset)."""
        result = subprocess.run(
            [
                "make", "-n", "--no-print-directory", "xnat-reset",
                "KIT=GSTT",
                f"XNAT_DATA_DIR={data_dir}",
                f"XNAT_CONTAINER_UID={expected_uid}",
                f"XNAT_CONTAINER_GID={expected_gid}",
            ],
            cwd=XNAT_DIR,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
        assert result.returncode == 0, f"make -n xnat-reset failed:\n{result.stderr}"
        lines = result.stdout.splitlines()
        start = next((i for i, ln in enumerate(lines) if ln.strip().startswith("set -e;")), None)
        assert start is not None, "xnat-reset emitted no `set -e` provisioning block"
        return "\n".join(lines[start:])

    def _run(self, tmp_path: Path, uid: int, gid: int) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ, PATH=f"{self._sudo_shim(tmp_path)}:{os.environ['PATH']}")
        recipe = self._dev_recipe(tmp_path / "data", uid, gid)
        return subprocess.run(
            ["sh", "-c", recipe], capture_output=True, text=True, timeout=TIMEOUT_SECONDS, env=env
        )

    def test_provisions_a_tree_the_container_uid_owns(self, tmp_path: Path) -> None:
        """The achievable case: the sequence creates the tree and its own guard then passes."""
        result = self._run(tmp_path, os.getuid(), os.getgid())
        assert result.returncode == 0, f"development branch failed on an achievable uid:\n{result.stdout}{result.stderr}"
        for name in ("tomcat_logs", "archive", "build", "cache"):
            created = tmp_path / "data" / "xnat-data" / name
            assert created.is_dir(), f"{name} was not provisioned"
            assert created.stat().st_uid == os.getuid()

    def test_an_unreachable_owner_stops_at_the_chown_not_the_guard(self, tmp_path: Path) -> None:
        """A chown the caller cannot perform must fail *as itself*, not as wrong ownership.

        Both the suppressed and the fatal form exit non-zero, so the exit status alone proves
        nothing — this asserts on *which* step reported. With ``chown ... 2>/dev/null || true`` the
        EPERM was discarded and the recipe walked on to a guard that could never pass, so the
        operator was told the directories had the wrong owner with no hint that the chown meant to
        fix it had silently failed. Failing at the chown names the cause instead.
        """
        result = self._run(tmp_path, os.getuid() + 1, os.getgid() + 1)
        assert result.returncode != 0, "a chown the developer cannot perform was swallowed"
        assert "Operation not permitted" in result.stderr, (
            "the chown's EPERM is being discarded; the recipe reports a symptom, not the cause"
        )
        assert "❌" not in result.stdout, (
            "the recipe continued past a failed chown to the ownership guard, which reports the "
            "wrong owner rather than the chown that could not set it"
        )
