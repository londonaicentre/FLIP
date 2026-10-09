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
"""pack-selftest-kit.sh: the operator's half of the delivered-kit self-test (FLIP#1390).

The real script runs against a stub `make` that lays out a dev kit the way each backend's
`provision` target does, CA and server private keys included, so the tests prove only the
client's files reach the tarball the node downloads.
"""

from __future__ import annotations

import os
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest
from conftest import AZURE_DIR

SCRIPT = AZURE_DIR / "scripts" / "pack-selftest-kit.sh"

DEV_KITS = {
    "nvflare": [
        "fl-services/nvflare/provision/workspace-dev/net-1/services/Trust_1/startup/fed_client.json",
        "fl-services/nvflare/provision/workspace-dev/net-1/services/Trust_1/startup/client.key",
        "fl-services/nvflare/provision/workspace-dev/net-1/services/Trust_1/local/resources.json",
        "fl-services/nvflare/provision/workspace-dev/net-1/services/Trust_2/startup/client.key",
        "fl-services/nvflare/provision/workspace-dev/net-1/services/fl-server-net-1/startup/server.key",
        "fl-services/nvflare/provision/workspace-dev/net-1/state/rootCA.key",
    ],
    "flower": [
        "fl-services/flower/provision/creds/net-1/certificates/ca.crt",
        "fl-services/flower/provision/creds/net-1/certificates/ca.key",
        "fl-services/flower/provision/creds/net-1/certificates/server.key",
        "fl-services/flower/provision/creds/net-1/keys/supernode_credentials_1",
        "fl-services/flower/provision/creds/net-1/keys/supernode_credentials_2",
    ],
}

EXPECTED = {
    "nvflare": {
        "fl-kit/net-1/services/Trust_1/startup/fed_client.json",
        "fl-kit/net-1/services/Trust_1/startup/client.key",
        "fl-kit/net-1/services/Trust_1/local/resources.json",
    },
    "flower": {
        "fl-kit/net-1/certificates/ca.crt",
        "fl-kit/net-1/keys/supernode_credentials_1",
    },
}


def _pack(tmp_path: Path, backend: str, *, provision_ok: bool = True) -> tuple[subprocess.CompletedProcess, Path, Path]:
    repo = tmp_path / "repo"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "make.log"
    lay_out = " ".join(f'"{repo}/{p}"' for p in DEV_KITS[backend])
    make = bin_dir / "make"
    make.write_text(
        "#!/bin/bash\n"
        f'echo "make $*" >> "{log}"\n'
        f"{'exit 1' if not provision_ok else ''}\n"
        f"for f in {lay_out}; do mkdir -p \"$(dirname \"$f\")\"; echo secret > \"$f\"; done\n"
    )
    make.chmod(make.stat().st_mode | stat.S_IEXEC)
    out = tmp_path / "out" / f"selftest-{backend}.tar.gz"
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FLIP_REPO_ROOT": str(repo)}
    result = subprocess.run(
        ["bash", str(SCRIPT), backend, str(out)], env=env, capture_output=True, text=True, timeout=60
    )
    return result, out, log


@pytest.mark.parametrize("backend", ["nvflare", "flower"])
def test_only_the_clients_files_are_packed(tmp_path, backend):
    result, out, log = _pack(tmp_path, backend)
    assert result.returncode == 0, result.stderr
    assert f"-C {tmp_path / 'repo'}/fl-services/{backend} provision" in log.read_text()
    with tarfile.open(out) as tar:
        files = {m.name.removeprefix("./") for m in tar.getmembers() if m.isfile()}
    assert files == EXPECTED[backend], "the tarball must carry Trust_1's client files and nothing else"


def test_a_failed_provision_writes_no_tarball(tmp_path):
    result, out, _ = _pack(tmp_path, "flower", provision_ok=False)
    assert result.returncode != 0
    assert not out.exists()


def test_rejects_an_unknown_backend(tmp_path):
    result = subprocess.run(["bash", str(SCRIPT), "pytorch", str(tmp_path / "x.tar.gz")], capture_output=True, text=True)
    assert result.returncode == 2
