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
"""Black-box tests for vm/templates/flip-node.sh with every system tool stubbed (FLIP#1390).

The helper runs as root on the node, so the tests point it at a temp dir through its env
overrides and put stub blkid/mkfs.ext4/mount/git/ansible-* commands first on PATH.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

from conftest import AZURE_DIR

SCRIPT = AZURE_DIR / "vm" / "templates" / "flip-node.sh"


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/bin/bash\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _setup(tmp_path: Path, *, disk_present: bool, has_fs: bool) -> tuple[dict, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    disk = tmp_path / "lun0"
    if disk_present:
        disk.write_text("")
    for tool in ("mkfs.ext4", "mount", "mountpoint", "ansible-galaxy", "ansible-playbook", "systemd-run", "chown"):
        _stub(bin_dir, tool, f'echo "{tool} $*" >> "{log}"; [ "{tool}" = mountpoint ] && exit 1; exit 0')
    _stub(bin_dir, "blkid", f'echo "blkid $*" >> "{log}"; exit {0 if has_fs else 2}')
    _stub(bin_dir, "git", f'echo "git $*" >> "{log}"; if [ "$1" = clone ]; then mkdir -p "${{@: -1}}/.git"; fi; exit 0')
    env_file = tmp_path / "node.env"
    env_file.write_text(
        "FLIP_REPO_URL=https://github.com/londonaicentre/FLIP.git\n"
        "FLIP_REF=0123456789abcdef0123456789abcdef01234567\n"
        "FLIP_ADMIN_USER=azureuser\nFL_BACKEND=nvflare\n"
    )
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FLIP_NODE_ENV": str(env_file),
        "FLIP_DIR": str(tmp_path / "opt-flip"),
        "FLIP_DISK_CANDIDATES": str(disk),
        "FLIP_DISK_WAIT_SECONDS": "1",
        "FLIP_FSTAB": str(tmp_path / "fstab"),
    }
    return env, log


def _run(env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60)


def test_flip_node_waits_for_disk(tmp_path):
    env, log = _setup(tmp_path, disk_present=False, has_fs=False)
    result = _run(env, "provision")
    assert result.returncode != 0
    assert "data disk" in result.stderr.lower()
    assert not log.exists() or "ansible-playbook" not in log.read_text(), "must not provision onto the OS disk"


def test_provision_formats_a_blank_disk_and_runs_the_play(tmp_path):
    env, log = _setup(tmp_path, disk_present=True, has_fs=False)
    result = _run(env, "provision")
    assert result.returncode == 0, result.stderr
    calls = log.read_text()
    assert "mkfs.ext4" in calls
    assert "git clone" in calls
    assert "0123456789abcdef0123456789abcdef01234567" in calls
    assert "ansible-galaxy install -r" in calls
    assert "ansible-playbook -i localhost, -c local" in calls
    assert "fl_backend=nvflare" in calls
    assert "LABEL=flipdata" in Path(env["FLIP_FSTAB"]).read_text()


def test_flip_node_never_reformats(tmp_path):
    env, log = _setup(tmp_path, disk_present=True, has_fs=True)
    assert _run(env, "provision").returncode == 0
    assert "mkfs.ext4" not in log.read_text()


def test_fstab_entry_is_not_duplicated(tmp_path):
    env, _ = _setup(tmp_path, disk_present=True, has_fs=True)
    _run(env, "provision")
    _run(env, "provision")
    assert Path(env["FLIP_FSTAB"]).read_text().count("LABEL=flipdata") == 1


def test_selftest_rejects_unknown_backend(tmp_path):
    env, _ = _setup(tmp_path, disk_present=True, has_fs=True)
    result = _run(env, "selftest", "pytorch")
    assert result.returncode != 0
    assert "nvflare or flower" in result.stderr


def test_selftest_starts_a_detached_unit(tmp_path):
    env, log = _setup(tmp_path, disk_present=True, has_fs=True)
    assert _run(env, "selftest", "flower").returncode == 0
    assert "systemd-run --unit=flip-selftest-flower" in log.read_text()


def test_unknown_command_prints_usage(tmp_path):
    env, _ = _setup(tmp_path, disk_present=True, has_fs=True)
    result = _run(env, "explode")
    assert result.returncode == 2
    assert "usage:" in result.stderr.lower()
