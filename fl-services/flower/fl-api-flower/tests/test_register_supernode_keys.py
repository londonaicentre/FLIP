# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""register-supernode-keys.sh checks the SuperLink certificate names the host it dials (FLIP#1738).

The one-shot is run for real with a stub `flwr` on PATH and a server certificate generated here. A
certificate without the SuperLink host among its SANs can never pass the readiness loop, so the script
must say so at once and name the fix, before any `flwr` call; one that names the host proceeds.
"""

import datetime
import os
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

SCRIPT = Path(__file__).resolve().parents[2] / "register-supernode-keys.sh"
HOST = "fl-server-net-1.flip.local"

# Every call is logged; `ls` answers "ready", `register` hands back a node id.
STUB_FLWR = """#!/usr/bin/env bash
echo "$@" >> "$FLWR_CALLS"
case "$2" in
  ls) echo '{"success": true, "list": []}' ;;
  register) echo '{"success": true, "node-id": 42}' ;;
esac
"""


def _write_server_cert(path: Path, sans: list[str]) -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(n) for n in sans]), critical=False)
        .sign(key, hashes.SHA256())
    )
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


@pytest.fixture
def one_shot(tmp_path):
    """Run the script against a kit whose server certificate carries the given SANs."""
    certs, bin_dir = tmp_path / "certs", tmp_path / "bin"
    certs.mkdir()
    bin_dir.mkdir()
    (certs / "ca.crt").write_text("unused by the stub\n")
    (bin_dir / "flwr").write_text(STUB_FLWR)
    (bin_dir / "flwr").chmod(0o755)
    # The script calls `python`; make this venv's interpreter (which has cryptography) answer to
    # that name. A wrapper rather than a symlink, so the interpreter still finds its venv.
    (bin_dir / "python").write_text(f'#!/usr/bin/env bash\nexec "{sys.executable}" "$@"\n')
    (bin_dir / "python").chmod(0o755)
    calls = tmp_path / "flwr-calls.log"

    def run(sans: list[str]) -> tuple[subprocess.CompletedProcess[str], str]:
        _write_server_cert(certs / "server.pem", sans)
        env = {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "HOME": str(tmp_path),
            "FLWR_CALLS": str(calls),
            "SUPERLINK_ADDRESS": f"{HOST}:8000",
            "SUPERLINK_ROOT_CERTIFICATES": str(certs / "ca.crt"),
            "TRUST_NAMES": '["Trust_1"]',
            "SUPERLINK_READY_ATTEMPTS": "1",
        }
        result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, cwd=tmp_path)
        return result, calls.read_text() if calls.exists() else ""

    return run


def test_a_certificate_without_the_superlink_host_is_refused_before_any_flwr_call(one_shot):
    result, calls = one_shot(["localhost", "superlink", "fl-server-net-1"])
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"does not name {HOST}" in result.stderr
    assert "localhost, superlink, fl-server-net-1" in result.stderr
    assert "FLOWER_EXTRA_SERVER_SANS" in result.stderr
    assert calls == "", "flwr must not be called when the certificate cannot work"


def test_a_certificate_naming_the_superlink_host_proceeds_to_registration(one_shot):
    result, calls = one_shot(["localhost", "superlink", "fl-server-net-1", HOST])
    assert "SuperLink Control API is ready." in result.stdout, result.stdout + result.stderr
    assert "supernode ls local" in calls
