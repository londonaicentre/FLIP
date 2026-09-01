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
        task = re.search(r"name:.*XNAT bind-mount directories.*?(?=\n    - name:)", text, re.DOTALL)
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
