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

"""generate_creds.py --add-supernode adds one SuperNode key pair to a deployed kit (FLIP#1942).

add-fl-kits mints through it, and the SuperLink only ever sees the public half: a pair whose halves did
not match would register, be handed out, and then have its SuperNode refused. So the script is run for
real here and checked to write a matching pair while leaving the CA and every other key untouched.
"""

import subprocess
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_ssh_private_key, load_ssh_public_key

SCRIPT = Path(__file__).resolve().parents[2] / "provision" / "scripts" / "generate_creds.py"


def _add(kit: Path, number: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--add-supernode", number], cwd=kit, capture_output=True, text=True
    )


def _kit(tmp_path: Path) -> Path:
    (tmp_path / "certificates").mkdir()
    (tmp_path / "certificates" / "ca.crt").write_text("the CA every trust already trusts\n")
    (tmp_path / "keys").mkdir()
    for half in ("", ".pub"):
        (tmp_path / "keys" / f"supernode_credentials_1{half}").write_text(f"key 1{half}\n")
    (tmp_path / "keys" / "supernode_credentials_10.pub").write_text("key 10.pub\n")
    return tmp_path


def test_adds_a_matching_pair_and_touches_nothing_else(tmp_path):
    kit = _kit(tmp_path)
    before = {p: p.read_bytes() for p in kit.rglob("*") if p.is_file()}

    result = _add(kit, "3")

    assert result.returncode == 0, result.stdout + result.stderr
    assert {p: p.read_bytes() for p in before} == before
    private = load_ssh_private_key((kit / "keys" / "supernode_credentials_3").read_bytes(), password=None)
    public = load_ssh_public_key((kit / "keys" / "supernode_credentials_3.pub").read_bytes())
    assert isinstance(private, ec.EllipticCurvePrivateKey)
    assert isinstance(private.curve, ec.SECP384R1)
    assert private.public_key().public_numbers() == public.public_numbers()
    assert (kit / "keys" / "supernode_credentials_3").stat().st_mode & 0o777 == 0o600


def test_refuses_an_existing_key_and_leaves_it_alone(tmp_path):
    kit = _kit(tmp_path)

    result = _add(kit, "1")

    assert result.returncode != 0
    assert "already exists" in result.stderr
    assert (kit / "keys" / "supernode_credentials_1").read_text() == "key 1\n"


def test_a_longer_number_with_the_same_prefix_does_not_block_a_key(tmp_path):
    """Key 10 exists; key 1 is removed, then added again: the check is on the exact names."""
    kit = _kit(tmp_path)
    for half in ("", ".pub"):
        (kit / "keys" / f"supernode_credentials_1{half}").unlink()

    assert _add(kit, "1").returncode == 0
    assert (kit / "keys" / "supernode_credentials_1.pub").exists()


def test_refuses_a_number_that_names_no_slot(tmp_path):
    kit = _kit(tmp_path)

    result = _add(kit, "0")

    assert result.returncode != 0
    assert not any((kit / "keys").glob("supernode_credentials_0*"))
