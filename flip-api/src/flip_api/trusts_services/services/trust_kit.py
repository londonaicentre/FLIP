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
"""Build a trust's downloadable kit (FLIP#1852).

A kit is what a trust node needs from the hub: its two identity keys, the hub-shared values and its
FL credentials. It is built in memory on request, encrypted under a passphrase generated here and
returned once, and never stored. ``trust/deploy/scripts/install_kit.py`` is the consumer.

A **full** kit carries the identity keys and is built only by the download that mints them, or by a
rotation: the hub keeps the api key's hash alone and cannot hand the keys over twice. Every other
download is an **update** kit, which leaves the trust's identity as it is.

The archive is a gzip tar holding ``kit.json`` and the FL files under ``fl-kit/``, encrypted the way
``openssl enc -aes-256-cbc -pbkdf2 -iter 600000 -md sha256`` does, so a trust host opens it with a
tool it already has.
"""

import base64
import hashlib
import io
import json
import os
import re
import secrets
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from cryptography.hazmat.primitives import hashes, padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from flip_api.config import get_settings
from flip_api.db.models.main_models import FLKitSlot, Trust
from flip_api.domain.schemas.types import FLBackend
from flip_api.scripts.register_trust import HUB_SHARED_ENV_KEYS
from flip_api.trusts_services.services.register_trust import RegisteredTrust
from flip_api.utils.encryption import get_aes_key
from flip_api.utils.s3_client import S3Client, parse_s3_path

KIT_SCHEMA = 1
PBKDF2_ITERATIONS = 600_000

# The files a FLARE client cannot start without; a slot missing one on any net is not handed out.
_FLARE_REQUIRED = ("client.crt", "client.key", "rootCA.pem", "fed_client.json", "sub_start.sh")

# Hub-shared keys that record what a site has installed. install-kit never overwrites them on a live
# site (the upgrade verb owns them), so a new hub build alone is not a kit update.
_INSTALLED_VERSION_KEYS = frozenset({"DOCKER_TAG", "DOCKER_FL_TAG"})


# A trust code names the kit file on the trust host (``trust/.env.<CODE>.<hub>``).
_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


class TrustKitError(Exception):
    """The hub cannot build this trust's kit; the message is safe to show the user."""


@dataclass(frozen=True)
class KitParts:
    """Everything in a kit but the identity keys, read before any key is minted."""

    backend: FLBackend
    shared: dict[str, str]
    files: dict[str, bytes]


@dataclass(frozen=True)
class BuiltKit:
    """An encrypted kit and the one copy of its passphrase."""

    file_name: str
    kind: Literal["full", "update"]
    passphrase: str
    content: bytes


class _KitSource:
    """The pre-provisioned FL kits: a directory or an ``s3://`` prefix that holds ``net-N/``."""

    def __init__(self, uri: str) -> None:
        self._uri = uri.rstrip("/")
        self._s3 = S3Client() if uri.startswith("s3://") else None

    def list(self, prefix: str) -> list[str]:
        """Paths of every file under ``prefix``, relative to the source root."""
        if self._s3 is None:
            base = Path(self._uri)
            return sorted(str(p.relative_to(base)) for p in (base / prefix).rglob("*") if p.is_file())
        _, root_key = parse_s3_path(self._uri)
        keys = (parse_s3_path(path)[1] for path in self._s3.list_objects(f"{self._uri}/{prefix}/"))
        return sorted(key[len(root_key) + 1 :] for key in keys)

    def read(self, path: str) -> bytes:
        """The bytes of one file, by its path relative to the source root."""
        if self._s3 is None:
            return (Path(self._uri) / path).read_bytes()
        return self._s3.get_object(f"{self._uri}/{path}")["Body"].read()


def hub_shared_values() -> dict[str, str]:
    """The hub-shared block of a kit, from this hub's own configuration.

    Returns:
        dict[str, str]: Every hub-shared key this hub has a value for.

    Raises:
        TrustKitError: A deployed hub is missing one, so the kit would not start a trust.
    """
    shared = {key: os.environ[key] for key in HUB_SHARED_ENV_KEYS if os.environ.get(key)}
    # The key the hub itself decrypts with, wherever it came from (Secrets Manager on a deployed hub).
    shared["AES_KEY_BASE64"] = base64.b64encode(get_aes_key()).decode()
    shared["FL_BACKEND"] = get_settings().FL_BACKEND.value
    if "FL_SERVER_PORT" in shared:
        shared.setdefault("FL_EDGE_PORT", shared["FL_SERVER_PORT"])

    missing = sorted(set(HUB_SHARED_ENV_KEYS) - set(shared))
    if missing and get_settings().ENV == "production":
        raise TrustKitError(f"This hub is not configured to issue kits: {', '.join(missing)} not set.")
    return shared


def hub_shared_digest(shared: dict[str, str]) -> str:
    """A digest that changes when a site needs a new kit: a hub-shared value moved, or the FL kits did.

    The FL credentials are covered by their kit date. The AES key enters as its hash, and the two
    installed-version keys are left out.
    """
    visible = {key: value for key, value in shared.items() if key not in _INSTALLED_VERSION_KEYS}
    visible["AES_KEY_BASE64"] = hashlib.sha256(visible["AES_KEY_BASE64"].encode()).hexdigest()
    return hashlib.sha256(json.dumps(visible, sort_keys=True).encode()).hexdigest()


def _fl_files(source: _KitSource, backend: FLBackend, slot_name: str, slot_number: int) -> dict[str, bytes]:
    """This slot's FL files on every net the source holds, keyed by their path inside the kit.

    Flower's are picked by name, never by folder: the same tree holds the SuperLink's private key and
    every other trust's.
    """
    files: dict[str, bytes] = {}
    net = 1
    while True:
        if backend is FLBackend.NVFLARE:
            slot_dir = f"net-{net}/services/{slot_name}"
            paths = [p for p in source.list(slot_dir) if p.startswith((f"{slot_dir}/startup/", f"{slot_dir}/local/"))]
            missing = [name for name in _FLARE_REQUIRED if f"{slot_dir}/startup/{name}" not in paths]
            if paths and missing:
                raise TrustKitError(
                    f"FL kit slot {slot_name} is incomplete on net-{net}: {', '.join(missing)} missing."
                )
        else:
            wanted = [f"net-{net}/certificates/ca.crt", f"net-{net}/keys/supernode_credentials_{slot_number}"]
            present = set(source.list(f"net-{net}/certificates")) | set(source.list(f"net-{net}/keys"))
            paths = [p for p in wanted if p in present]
            if paths and len(paths) != len(wanted):
                raise TrustKitError(f"FL kit slot {slot_name} is incomplete on net-{net}.")
        if not paths:
            break
        files.update({f"fl-kit/{path}": source.read(path) for path in paths})
        net += 1

    if not files:
        raise TrustKitError(f"No FL kit was found for slot {slot_name}.")
    return files


def _archive(kit_json: dict[str, Any], files: dict[str, bytes]) -> bytes:
    """The kit as a gzip tar: ``kit.json`` first, then the FL files."""
    members = {"kit.json": json.dumps(kit_json, indent=2).encode(), **files}
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o600
            info.mtime = int(datetime.now(timezone.utc).timestamp())
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def encrypt(plaintext: bytes, passphrase: str) -> bytes:
    """Encrypt as ``openssl enc -aes-256-cbc -pbkdf2 -iter 600000 -md sha256 -salt`` does."""
    salt = secrets.token_bytes(8)
    derived = PBKDF2HMAC(algorithm=hashes.SHA256(), length=48, salt=salt, iterations=PBKDF2_ITERATIONS).derive(
        passphrase.encode()
    )
    padder = padding.PKCS7(128).padder()
    encryptor = Cipher(algorithms.AES(derived[:32]), modes.CBC(derived[32:])).encryptor()
    padded = padder.update(plaintext) + padder.finalize()
    return b"Salted__" + salt + encryptor.update(padded) + encryptor.finalize()


def load_kit_parts(trust: Trust, slot: FLKitSlot) -> KitParts:
    """Read the hub-shared values and the slot's FL files.

    Called before keys are minted, so that a kit which cannot be built never uses up the one
    download that carries them.

    Args:
        trust (Trust): The trust.
        slot (FLKitSlot): Its FL kit slot.

    Returns:
        KitParts: The parts ``build_kit`` assembles.

    Raises:
        TrustKitError: The trust's code cannot name a kit file, the hub is not configured to issue
            kits, or the slot's FL files are missing or incomplete.
    """
    if not trust.code or not _CODE.fullmatch(trust.code):
        raise TrustKitError("This trust's code cannot name a kit file; it must be letters, digits, - or _.")
    settings = get_settings()
    if not settings.FL_KITS_URI:
        raise TrustKitError("This hub is not configured to issue kits: FL_KITS_URI not set.")
    shared = hub_shared_values()
    files = _fl_files(_KitSource(settings.FL_KITS_URI), settings.FL_BACKEND, slot.slot_name, slot.slot_number)
    return KitParts(backend=settings.FL_BACKEND, shared=shared, files=files)


def build_kit(registered: RegisteredTrust, parts: KitParts) -> BuiltKit:
    """Assemble and encrypt a trust's kit.

    Args:
        registered (RegisteredTrust): The trust and its FL kit slot. With plaintext keys it yields a
            full kit; without, an update kit.
        parts (KitParts): From ``load_kit_parts``.

    Returns:
        BuiltKit: The encrypted archive and its passphrase. Neither is kept anywhere.
    """
    trust, slot = registered.trust, registered.fl_kit_slot
    kit: dict[str, Any] = {
        "trust_id": str(trust.id),
        "trust_name": trust.name,
        "fl_kit_slot": slot.slot_name,
        "fl_kit_slot_number": slot.slot_number,
        "hub_shared": parts.shared,
    }
    kind: Literal["full", "update"] = "update"
    if registered.trust_api_key and registered.trust_internal_service_key:
        kind = "full"
        kit["trust_api_key"] = registered.trust_api_key
        kit["trust_internal_service_key"] = registered.trust_internal_service_key

    now = datetime.now(timezone.utc)
    kit_json = {
        "schema": KIT_SCHEMA,
        "kind": kind,
        "issued_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fl_backend": parts.backend.value,
        "trust": {"id": str(trust.id), "name": trust.name, "code": trust.code, "region": trust.region},
        "kit": kit,
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in parts.files.items()},
    }

    passphrase = secrets.token_urlsafe(24)
    return BuiltKit(
        file_name=f"flip-trust-kit-{trust.code}-{now.strftime('%Y%m%d-%H%M%S')}.tar.gz.enc",
        kind=kind,
        passphrase=passphrase,
        content=encrypt(_archive(kit_json, parts.files), passphrase),
    )
