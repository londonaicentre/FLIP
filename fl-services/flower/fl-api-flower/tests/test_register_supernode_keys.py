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

    def run(sans: list[str], **overrides: str) -> tuple[subprocess.CompletedProcess[str], str]:
        _write_server_cert(certs / "server.pem", sans)
        env = {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "HOME": str(tmp_path),
            "FLWR_CALLS": str(calls),
            "SUPERLINK_ADDRESS": f"{HOST}:8000",
            "SUPERLINK_ROOT_CERTIFICATES": str(certs / "ca.crt"),
            "TRUST_NAMES": '["Trust_1"]',
            "SUPERLINK_READY_ATTEMPTS": "1",
            **overrides,
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


def _keys(tmp_path: Path, *numbers: int) -> Path:
    keys = tmp_path / "keys"
    keys.mkdir()
    for number in numbers:
        (keys / f"supernode_credentials_{number}.pub").write_text("unused by the stub\n")
    return keys


def test_without_trust_names_each_key_is_named_after_its_own_number(one_shot, tmp_path):
    """FLIP#1942: the keys are the slot list, so the tenth key is Trust_10 although it sorts second."""
    keys = _keys(tmp_path, 1, 2, 10)
    result, calls = one_shot([HOST], TRUST_NAMES="", KEYS_DIR=str(keys))
    registered = [line.split() for line in calls.splitlines() if line.startswith("supernode register")]
    assert [(Path(call[2]).name, call[call.index("--name") + 1]) for call in registered] == [
        ("supernode_credentials_1.pub", "Trust_1"),
        ("supernode_credentials_2.pub", "Trust_2"),
        ("supernode_credentials_10.pub", "Trust_10"),
    ], result.stdout + result.stderr


def test_without_trust_names_a_key_that_names_no_slot_is_refused(one_shot, tmp_path):
    keys = _keys(tmp_path, 1)
    (keys / "stray.pub").write_text("unused by the stub\n")
    result, calls = one_shot([HOST], TRUST_NAMES="", KEYS_DIR=str(keys))
    assert result.returncode == 1
    assert "stray.pub is not supernode_credentials_<N>.pub" in result.stderr
    assert "supernode register" not in calls


def test_a_registration_the_superlink_did_not_accept_fails_the_run(one_shot, tmp_path):
    """Exit 0 with success false left the key unregistered while the one-shot reported success."""
    keys = _keys(tmp_path, 1)
    flwr = tmp_path / "bin" / "flwr"
    flwr.write_text(STUB_FLWR.replace('{"success": true, "node-id": 42}', '{"success": false, "error-message": "x"}'))
    result, _ = one_shot([HOST], TRUST_NAMES="", KEYS_DIR=str(keys))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "One or more registrations failed" in result.stderr


# A SuperLink that already holds keys 1 and 2 as nodes 101 and 102: `ls` lists them, `register`
# refuses those two keys as Flower does (exit 0, success false) and gives any other key node 42.
# The FL API mapping is captured by a `python` stub that logs the POSTed name and node id.
STUB_FLWR_HOLDING = """#!/usr/bin/env bash
echo "$@" >> "$FLWR_CALLS"
case "$2" in
  ls) echo '{"success": true, "nodes": [{"node-id": "101"}, {"node-id": "102"}]}' ;;
  register)
    case "$3" in
      *_1.pub|*_2.pub) echo '{"success": false, "error-message": "[code: 23] Public key already in use."}' ;;
      *) echo '{"success": true, "node-id": 42}' ;;
    esac ;;
esac
"""
STUB_PYTHON_MAPPINGS = """#!/usr/bin/env bash
if [ "$1" = "-c" ]; then
  case "$2" in
    *register_node*) echo "$2" | grep -o "'name': '[^']*', 'node_id': '[^']*'" >> "$MAPPINGS" ;;
  esac
  exit 0
fi
exec "{python}" "$@"
"""


def _holding_superlink(tmp_path: Path, flwr: str = STUB_FLWR_HOLDING) -> Path:
    (tmp_path / "bin" / "flwr").write_text(flwr)
    (tmp_path / "bin" / "python").write_text(STUB_PYTHON_MAPPINGS.replace("{python}", sys.executable))
    return tmp_path / "mappings.log"


def test_keys_already_registered_are_skipped_and_keep_their_node_ids(one_shot, tmp_path):
    """Every add-fl-kits run re-registers the keys a running SuperLink already holds: they must be
    skipped, not fail the run, and keep the node ids the SuperLink lists for them, in order."""
    keys = _keys(tmp_path, 1, 2, 10)
    mappings = _holding_superlink(tmp_path)
    result, _ = one_shot(
        [HOST], TRUST_NAMES="", KEYS_DIR=str(keys), FL_API_ADDRESS="http://fl-api:8000", MAPPINGS=str(mappings)
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "All SuperNode keys registered successfully." in result.stdout
    assert "OK: supernode_credentials_10.pub node_id=42" in result.stdout
    assert mappings.read_text().splitlines() == [
        "'name': 'Trust_1', 'node_id': '101'",
        "'name': 'Trust_2', 'node_id': '102'",
        "'name': 'Trust_10', 'node_id': '42'",
    ]


def test_node_ids_that_cannot_be_matched_to_their_keys_are_not_guessed(one_shot, tmp_path):
    """The SuperLink lists more nodes than the keys it refused (a node whose key left the disk): matching
    by position would send the FL API the wrong trust for each id, so those mappings are left alone."""
    keys = _keys(tmp_path, 1, 2, 10)
    mappings = _holding_superlink(
        tmp_path,
        STUB_FLWR_HOLDING.replace('{"node-id": "102"}]', '{"node-id": "102"}, {"node-id": "103"}]'),
    )
    result, _ = one_shot(
        [HOST], TRUST_NAMES="", KEYS_DIR=str(keys), FL_API_ADDRESS="http://fl-api:8000", MAPPINGS=str(mappings)
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "their FL API mappings are left as they are" in result.stdout
    assert mappings.read_text().splitlines() == ["'name': 'Trust_10', 'node_id': '42'"]


def test_a_reply_without_a_node_id_fails_the_run(one_shot, tmp_path):
    """Output this script does not recognise used to pass as a warning, releasing a slot whose key
    may not be registered."""
    keys = _keys(tmp_path, 1)
    flwr = tmp_path / "bin" / "flwr"
    flwr.write_text(STUB_FLWR.replace('{"success": true, "node-id": 42}', "registered, probably"))
    result, _ = one_shot([HOST], TRUST_NAMES="", KEYS_DIR=str(keys))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "All SuperNode keys registered successfully." not in result.stdout


# FLIP#2047: the node → trust name mappings live only in the FL API's memory and only this one-shot
# writes them, so an FL API that did not take them must fail the run instead of passing as a warning.
def _failing_fl_api(tmp_path: Path, case: str) -> None:
    """The mapping-logging `python` stub, with one case tried first for the call the FL API fails."""
    stub = STUB_PYTHON_MAPPINGS.replace("    *register_node*)", f"    {case}\n    *register_node*)")
    (tmp_path / "bin" / "python").write_text(stub.replace("{python}", sys.executable))


def test_an_fl_api_that_never_answers_fails_the_run(one_shot, tmp_path):
    keys = _keys(tmp_path, 1)
    _failing_fl_api(tmp_path, "*openapi.json*) exit 1 ;;")
    result, _ = one_shot(
        [HOST], TRUST_NAMES="", KEYS_DIR=str(keys), FL_API_ADDRESS="http://fl-api:8000", FL_API_READY_ATTEMPTS="1"
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "All SuperNode keys registered successfully." in result.stdout
    assert "FL API at http://fl-api:8000 did not become ready" in result.stderr


def test_a_mapping_the_fl_api_refuses_fails_the_run_after_the_others_are_tried(one_shot, tmp_path):
    keys = _keys(tmp_path, 1, 2, 3)
    mappings = tmp_path / "mappings.log"
    _failing_fl_api(tmp_path, """*"'name': 'Trust_2'"*) echo "HTTP Error 401: Unauthorized" >&2; exit 1 ;;""")
    result, _ = one_shot(
        [HOST], TRUST_NAMES="", KEYS_DIR=str(keys), FL_API_ADDRESS="http://fl-api:8000", MAPPINGS=str(mappings)
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "did not take the mapping for: Trust_2 " in result.stderr
    assert "HTTP Error 401: Unauthorized" in result.stderr
    assert [line.split("'")[3] for line in mappings.read_text().splitlines()] == ["Trust_1", "Trust_3"]


def test_without_an_fl_api_address_the_mappings_are_skipped(one_shot, tmp_path):
    keys = _keys(tmp_path, 1)
    result, _ = one_shot([HOST], TRUST_NAMES="", KEYS_DIR=str(keys))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FL_API_ADDRESS not set" in result.stdout
