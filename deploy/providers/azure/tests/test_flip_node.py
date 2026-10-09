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

import io
import os
import stat
import subprocess
import tarfile
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
        "KIT_DROP_URL=https://flipazkit01234567.blob.core.windows.net/kits\n"
    )
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FLIP_NODE_ENV": str(env_file),
        "FLIP_DIR": str(tmp_path / "opt-flip"),
        "FLIP_DISK_CANDIDATES": str(disk),
        "FLIP_DISK_WAIT_SECONDS": "1",
        "FLIP_FSTAB": str(tmp_path / "fstab"),
        "FLIP_NODE_BIN": str(tmp_path / "installed-flip-node"),
        "FLIP_FETCH_BACKOFF_SECONDS": "0",
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


# ── fetch-kit: the node downloads its kit from the kit drop with its own identity ──────────


def _tarball(path: Path, members: dict[str, str]) -> Path:
    with tarfile.open(path, "w:gz") as tar:
        for name, text in members.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def _stub_curl(env: dict, tmp_path: Path, tarball: Path, *, refusals: int = 0) -> Path:
    """IMDS answers with a token; the blob endpoint refuses `refusals` times with 403, then serves `tarball`."""
    log = tmp_path / "curl.log"
    count = tmp_path / "blob.count"
    _stub(
        Path(env["PATH"].split(":")[0]),
        "curl",
        f'''echo "curl $*" >> "{log}"
out=""; prev=""
for a in "$@"; do [ "$prev" = -o ] && out="$a"; prev="$a"; done
case "$*" in
  *169.254.169.254*) echo \'{{"access_token":"tok-abc"}}\'; exit 0;;
esac
n=$(( $(cat "{count}" 2>/dev/null || echo 0) + 1 )); echo $n > "{count}"
if [ "$n" -le {refusals} ]; then printf 403; exit 0; fi
cp "{tarball}" "$out"; printf 200''',
    )
    _stub(Path(env["PATH"].split(":")[0]), "sleep", "exit 0")
    return log


def test_fetch_kit_extracts_into_the_kits_dir_and_deletes_the_tarball(tmp_path):
    env, _ = _setup(tmp_path, disk_present=True, has_fs=True)
    tarball = _tarball(tmp_path / "src.tar.gz", {".env.Trust_3": "TRUST_API_KEY=x\n", "fl-kit/net-1/a.txt": "kit"})
    log = _stub_curl(env, tmp_path, tarball)
    result = _run(env, "fetch-kit", "flip-trust-kit-Trust_3.tar.gz")
    assert result.returncode == 0, result.stderr
    dest = Path(env["FLIP_DIR"]) / "kits" / "flip-trust-kit-Trust_3"
    assert (dest / "fl-kit" / "net-1" / "a.txt").read_text() == "kit"
    assert (dest / ".env.Trust_3").exists()
    assert oct(dest.stat().st_mode & 0o777) == "0o700", "kits hold secrets: the dir is owner-only"
    assert not list((Path(env["FLIP_DIR"]) / "kits").glob("*.tar.gz")), "the tarball is deleted after extraction"
    calls = log.read_text()
    assert "resource=https://storage.azure.com/" in calls, "the token must be for Storage"
    assert "Authorization: Bearer tok-abc" in calls
    assert "https://flipazkit01234567.blob.core.windows.net/kits/flip-trust-kit-Trust_3.tar.gz" in calls


def test_fetch_kit_refuses_a_member_that_escapes_the_kit_dir(tmp_path):
    env, _ = _setup(tmp_path, disk_present=True, has_fs=True)
    tarball = _tarball(tmp_path / "src.tar.gz", {"../../escaped.txt": "pwned"})
    _stub_curl(env, tmp_path, tarball)
    result = _run(env, "fetch-kit", "evil.tar.gz")
    assert result.returncode != 0
    assert "unsafe" in result.stderr.lower()
    assert not list(tmp_path.rglob("escaped.txt")), "nothing may be written outside the kit dir"


def test_fetch_kit_waits_out_role_propagation(tmp_path):
    env, _ = _setup(tmp_path, disk_present=True, has_fs=True)
    tarball = _tarball(tmp_path / "src.tar.gz", {"fl-kit/x": "1"})
    _stub_curl(env, tmp_path, tarball, refusals=2)
    result = _run(env, "fetch-kit", "selftest-nvflare.tar.gz")
    assert result.returncode == 0, result.stderr
    assert (Path(env["FLIP_DIR"]) / "kits" / "selftest-nvflare" / "fl-kit" / "x").exists()


def test_fetch_kit_gives_up_naming_the_likely_causes(tmp_path):
    env, _ = _setup(tmp_path, disk_present=True, has_fs=True)
    env["FLIP_FETCH_ATTEMPTS"] = "3"
    tarball = _tarball(tmp_path / "src.tar.gz", {"fl-kit/x": "1"})
    _stub_curl(env, tmp_path, tarball, refusals=99)
    result = _run(env, "fetch-kit", "selftest-nvflare.tar.gz")
    assert result.returncode != 0
    assert "HTTP 403" in result.stderr and "role" in result.stderr.lower()


def test_fetch_kit_rejects_a_name_with_a_path(tmp_path):
    env, _ = _setup(tmp_path, disk_present=True, has_fs=True)
    result = _run(env, "fetch-kit", "../other/kit.tar.gz")
    assert result.returncode == 2
    assert "tar.gz" in result.stderr


def test_fetch_kit_needs_a_kit_drop(tmp_path):
    env, _ = _setup(tmp_path, disk_present=True, has_fs=True)
    Path(env["FLIP_NODE_ENV"]).write_text(Path(env["FLIP_NODE_ENV"]).read_text().replace(
        "KIT_DROP_URL=https://flipazkit01234567.blob.core.windows.net/kits", "KIT_DROP_URL="))
    result = _run(env, "fetch-kit", "selftest-nvflare.tar.gz")
    assert result.returncode != 0
    assert "kit drop" in result.stderr.lower()


def test_provision_refreshes_flip_node_from_the_checkout(tmp_path):
    """cloud-init installs flip-node once; a reprovision to a new ref must bring its flip-node too."""
    env, _ = _setup(tmp_path, disk_present=True, has_fs=True)
    repo_copy = Path(env["FLIP_DIR"]) / "FLIP" / "deploy" / "providers" / "azure" / "vm" / "templates" / "flip-node.sh"
    repo_copy.parent.mkdir(parents=True)
    repo_copy.write_text("#!/bin/bash\necho new\n")
    assert _run(env, "provision").returncode == 0
    installed = Path(env["FLIP_NODE_BIN"])
    assert installed.read_text() == "#!/bin/bash\necho new\n"
    assert installed.stat().st_mode & stat.S_IXUSR
