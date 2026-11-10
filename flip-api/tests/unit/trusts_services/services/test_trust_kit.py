# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

"""The trust kit builder (FLIP#1852): what goes in a kit, what must never, and that openssl opens it."""

import base64
import io
import json
import shutil
import subprocess
import tarfile
from pathlib import Path
from uuid import uuid4

import boto3
import pytest
from cryptography.hazmat.primitives import hashes, padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from moto import mock_aws

from flip_api.config import get_settings
from flip_api.db.models.main_models import FLKitSlot, Trust
from flip_api.domain.schemas.types import FLBackend
from flip_api.scripts.register_trust import HUB_SHARED_ENV_KEYS
from flip_api.trusts_services.services import trust_kit
from flip_api.trusts_services.services.register_trust import RegisteredTrust
from flip_api.trusts_services.services.trust_kit import (
    TrustKitError,
    build_kit,
    hub_shared_digest,
    hub_shared_values,
    load_kit_parts,
)

FLARE_STARTUP = ("client.crt", "client.key", "rootCA.pem", "fed_client.json", "sub_start.sh", "start.sh")
AES_KEY = base64.b64encode(b"k" * 32).decode()


def _write(root: Path, files: dict[str, str]) -> None:
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)


@pytest.fixture
def flare_kits(tmp_path: Path) -> Path:
    """Two nets, two slots, plus the server's kit that no trust may receive."""
    files = {}
    for net in (1, 2):
        for slot in ("Trust_1", "Trust_2"):
            base = f"net-{net}/services/{slot}"
            files.update({f"{base}/startup/{name}": f"{slot}-{net}-{name}" for name in FLARE_STARTUP})
            files[f"{base}/local/resources.json.default"] = "{}"
            files[f"{base}/readme.txt"] = "readme"
        files[f"net-{net}/services/fl-server-net-{net}/startup/server.key"] = "SERVER-KEY"
        files[f"net-{net}/state/cert.json"] = "CA-STATE"
    _write(tmp_path, files)
    return tmp_path


@pytest.fixture
def flower_kits(tmp_path: Path) -> Path:
    """One net with the SuperLink's key and two SuperNodes' keys."""
    _write(
        tmp_path,
        {
            "net-1/certificates/ca.crt": "CA",
            "net-1/certificates/server.key": "SERVER-KEY",
            "net-1/certificates/server.pem": "SERVER-CERT",
            "net-1/keys/supernode_credentials_1": "KEY-1",
            "net-1/keys/supernode_credentials_1.pub": "PUB-1",
            "net-1/keys/supernode_credentials_2": "KEY-2",
            "net-1/keys/supernode_credentials_2.pub": "PUB-2",
        },
    )
    return tmp_path


@pytest.fixture
def hub(monkeypatch):
    """A hub configured to issue kits: every hub-shared value set, and a known AES key."""
    for key in HUB_SHARED_ENV_KEYS:
        monkeypatch.setenv(key, f"value-of-{key}")
    monkeypatch.setattr(trust_kit, "get_aes_key", lambda: base64.b64decode(AES_KEY))

    def configure(uri: str | None, backend: FLBackend = FLBackend.NVFLARE) -> None:
        monkeypatch.setattr(get_settings(), "FL_KITS_URI", uri)
        monkeypatch.setattr(get_settings(), "FL_BACKEND", backend)

    return configure


def _registered(number: int = 2, keys: bool = False, code: str | None = "BDMS") -> RegisteredTrust:
    trust = Trust(id=uuid4(), name="Bangkok Dusit", code=code, region=None)
    return RegisteredTrust(
        trust=trust,
        fl_kit_slot=FLKitSlot(slot_name=f"Trust_{number}", slot_number=number, assigned_to_trust_id=trust.id),
        trust_api_key="api-key" if keys else None,  # pragma: allowlist secret
        trust_internal_service_key="internal-key" if keys else None,  # pragma: allowlist secret
    )


def _build(registered: RegisteredTrust) -> trust_kit.BuiltKit:
    return build_kit(registered, load_kit_parts(registered.trust, registered.fl_kit_slot))


def _open(kit: trust_kit.BuiltKit) -> dict[str, bytes]:
    """Decrypt as the documented openssl command would, and return the tar members."""
    assert kit.content[:8] == b"Salted__"
    derived = PBKDF2HMAC(algorithm=hashes.SHA256(), length=48, salt=kit.content[8:16], iterations=600_000).derive(
        kit.passphrase.encode()
    )
    decryptor = Cipher(algorithms.AES(derived[:32]), modes.CBC(derived[32:])).decryptor()
    unpadder = padding.PKCS7(128).unpadder()
    plain = unpadder.update(decryptor.update(kit.content[16:]) + decryptor.finalize()) + unpadder.finalize()
    with tarfile.open(fileobj=io.BytesIO(plain), mode="r:gz") as tar:
        return {m.name: tar.extractfile(m).read() for m in tar.getmembers()}  # type: ignore[union-attr]


def test_flare_update_kit_holds_only_this_slots_startup_and_local(flare_kits, hub):
    hub(str(flare_kits))

    kit = _build(_registered(number=2))

    members = _open(kit)
    kit_json = json.loads(members.pop("kit.json"))
    assert kit.kind == kit_json["kind"] == "update"
    assert kit.file_name.startswith("flip-trust-kit-BDMS-")
    assert kit.file_name.endswith(".tar.gz.enc")
    assert sorted(members) == sorted(
        [f"fl-kit/net-{net}/services/Trust_2/startup/{name}" for net in (1, 2) for name in FLARE_STARTUP]
        + [f"fl-kit/net-{net}/services/Trust_2/local/resources.json.default" for net in (1, 2)]
    )
    assert members["fl-kit/net-2/services/Trust_2/startup/client.key"] == b"Trust_2-2-client.key"
    assert set(kit_json["files"]) == set(members)
    assert "trust_api_key" not in kit_json["kit"]
    assert "trust_internal_service_key" not in kit_json["kit"]
    assert kit_json["kit"]["fl_kit_slot"] == "Trust_2"
    assert kit_json["kit"]["fl_kit_slot_number"] == 2
    assert kit_json["kit"]["hub_shared"]["AES_KEY_BASE64"] == AES_KEY
    assert kit_json["trust"]["code"] == "BDMS"
    assert kit_json["fl_backend"] == "nvflare"


def test_full_kit_carries_the_identity_keys(flare_kits, hub):
    hub(str(flare_kits))

    kit_json = json.loads(_open(_build(_registered(keys=True)))["kit.json"])

    assert kit_json["kind"] == "full"
    assert kit_json["kit"]["trust_api_key"] == "api-key"  # pragma: allowlist secret
    assert kit_json["kit"]["trust_internal_service_key"] == "internal-key"  # pragma: allowlist secret


def test_flower_kit_holds_the_ca_cert_and_this_supernodes_key_only(flower_kits, hub):
    """The SuperLink's key and the other SuperNodes' sit in the same folders (FLIP#1027)."""
    hub(str(flower_kits), FLBackend.FLOWER)

    members = _open(_build(_registered(number=2)))

    assert sorted(members) == [
        "fl-kit/net-1/certificates/ca.crt",
        "fl-kit/net-1/keys/supernode_credentials_2",
        "kit.json",
    ]
    assert members["fl-kit/net-1/keys/supernode_credentials_2"] == b"KEY-2"


@mock_aws
def test_kits_are_read_from_an_s3_prefix(flower_kits, hub):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="kits")
    for path in flower_kits.rglob("*"):
        if path.is_file():
            s3.put_object(Bucket="kits", Key=f"flower/20261101/{path.relative_to(flower_kits)}", Body=path.read_bytes())
    hub("s3://kits/flower/20261101", FLBackend.FLOWER)

    members = _open(_build(_registered(number=1)))

    assert members["fl-kit/net-1/keys/supernode_credentials_1"] == b"KEY-1"
    assert "fl-kit/net-1/keys/supernode_credentials_2" not in members


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not installed")
def test_the_documented_openssl_command_opens_the_kit(flare_kits, hub, tmp_path):
    hub(str(flare_kits))
    kit = _build(_registered())
    (tmp_path / "kit.enc").write_bytes(kit.content)

    result = subprocess.run(
        ["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "600000", "-md", "sha256", "-pass", "stdin"]
        + ["-in", str(tmp_path / "kit.enc")],
        input=kit.passphrase.encode(),
        capture_output=True,
        check=True,
    )

    with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:gz") as tar:
        assert "kit.json" in tar.getnames()


def test_each_kit_has_its_own_passphrase(flare_kits, hub):
    hub(str(flare_kits))

    first, second = _build(_registered()), _build(_registered())

    assert first.passphrase != second.passphrase
    assert len(first.passphrase) >= 32
    assert first.passphrase.encode() not in first.content


@pytest.mark.parametrize(
    ("configure", "registered", "message"),
    [
        (lambda hub, kits: hub(None), _registered(), "FL_KITS_URI not set"),
        (lambda hub, kits: hub(str(kits)), _registered(code=None), "code cannot name a kit file"),
        (lambda hub, kits: hub(str(kits)), _registered(code="../etc"), "code cannot name a kit file"),
        (lambda hub, kits: hub(str(kits)), _registered(number=9), "No FL kit was found for slot Trust_9"),
    ],
)
def test_a_kit_that_cannot_be_built_says_why(flare_kits, hub, configure, registered, message):
    configure(hub, flare_kits)

    with pytest.raises(TrustKitError, match=message):
        load_kit_parts(registered.trust, registered.fl_kit_slot)


def test_an_incomplete_flare_slot_is_not_handed_out(flare_kits, hub):
    (flare_kits / "net-2/services/Trust_2/startup/client.key").unlink()
    hub(str(flare_kits))

    with pytest.raises(TrustKitError, match="Trust_2 is incomplete on net-2: client.key missing"):
        _build(_registered(number=2))


def test_a_flower_slot_without_its_key_is_not_handed_out(flower_kits, hub):
    hub(str(flower_kits), FLBackend.FLOWER)

    with pytest.raises(TrustKitError, match="incomplete on net-1"):
        _build(_registered(number=3))


def test_a_deployed_hub_refuses_to_issue_a_kit_with_a_hub_shared_value_missing(hub, monkeypatch):
    hub("unused")
    monkeypatch.delenv("NLB_SUBDOMAIN")
    monkeypatch.setattr(get_settings(), "ENV", "production")

    with pytest.raises(TrustKitError, match="NLB_SUBDOMAIN not set"):
        hub_shared_values()


def test_a_dev_hub_issues_what_it_has(hub, monkeypatch):
    hub("unused")
    monkeypatch.delenv("NLB_SUBDOMAIN")
    monkeypatch.delenv("FL_EDGE_PORT")

    shared = hub_shared_values()

    assert "NLB_SUBDOMAIN" not in shared
    assert shared["FL_EDGE_PORT"] == shared["FL_SERVER_PORT"]


def test_digest_moves_with_a_hub_shared_value_but_not_with_the_image_tags(hub):
    hub("unused")
    shared = hub_shared_values()
    digest = hub_shared_digest(shared)

    assert hub_shared_digest({**shared, "DOCKER_TAG": "v9.9.9", "DOCKER_FL_TAG": "v9.9.9"}) == digest
    assert hub_shared_digest({**shared, "FLARE_KIT_DATE": "20991231"}) != digest
    assert hub_shared_digest({**shared, "AES_KEY_BASE64": base64.b64encode(b"n" * 32).decode()}) != digest
    assert AES_KEY not in digest
