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

"""Authenticated encryption for hub-trust payloads.

AES-256-GCM (AEAD) in a small, versioned, key-id'd envelope. GCM authenticates the
ciphertext *and* the envelope's version and key id, so tampering with any of them raises
``InvalidTag`` instead of decrypting to attacker-influenced plaintext, which is what the
previous AES-CBC scheme did (it had no authentication, and a CBC ciphertext is malleable).

Wire format: base64 of ``{"v": 1, "kid": "shared", "iv": <b64 nonce>, "ct": <b64 ciphertext||tag>}``,
with a 12-byte nonce. ``v`` is a format discriminator so the envelope can change later
without ambiguity; the algorithm is fixed rather than negotiated, which leaves no
algorithm-confusion surface. ``kid`` names the key the payload was encrypted under. Today
every service holds exactly one key, the platform-wide ``AES_KEY_BASE64`` registered as
:data:`SHARED_KID`; per-trust keys (FLIP#845) add entries to the keyring and change
nothing on the wire.

There is deliberately no fallback to the pre-FLIP#1179 CBC format: an unauthenticated
ciphertext is never accepted, so the hub and every trust move to this build together.
"""

import base64
import json
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from data_access_api.config import get_settings


def get_aes_key() -> bytes:
    """Retrieve the AES key from the environment file and return it as bytes.

    Returns:
        bytes: The decoded AES key (16, 24, or 32 bytes).

    Raises:
        ValueError: If the AES key is missing from configuration or has an invalid length.
    """
    key_b64 = get_settings().AES_KEY_BASE64
    if not key_b64:
        raise ValueError("AES key not found in environment file")

    key = base64.b64decode(key_b64)
    if len(key) not in (16, 24, 32):
        raise ValueError("Invalid AES key length")
    return key


#: Key id of the platform-wide shared key (``AES_KEY_BASE64``).
SHARED_KID = "shared"

_VERSION = 1
_NONCE_BYTES = 12  # NIST SP 800-38D recommended nonce length for AES-GCM
_ENVELOPE_FIELDS = frozenset({"v", "kid", "iv", "ct"})


def _keyring() -> dict[str, bytes]:
    """Return the ``kid -> key`` map payloads are encrypted under and resolved against.

    One entry today. Per-trust keys (FLIP#845) are added here, which is why :func:`decrypt`
    resolves the key from the envelope's ``kid`` rather than assuming the shared one.
    """
    return {SHARED_KID: get_aes_key()}


def _aad(kid: str) -> bytes:
    """Associated data bound into the GCM tag, so neither the version nor the ``kid`` can be altered."""
    return f"FLIP|v{_VERSION}|{kid}".encode()


def encrypt(plaintext: str, key: bytes | None = None) -> str:
    """Encrypt ``plaintext`` with AES-256-GCM under the shared key.

    Args:
        plaintext (str): The text to encrypt.
        key (bytes | None): Explicit key. If ``None``, the shared key from :func:`get_aes_key`.

    Returns:
        str: Base64-encoded envelope (see the module docstring for the format).

    Raises:
        ValueError: The key is not a valid AES key length.
    """
    kid = SHARED_KID
    if key is None:
        key = _keyring()[kid]

    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode(), _aad(kid))
    envelope = {
        "v": _VERSION,
        "kid": kid,
        "iv": base64.b64encode(nonce).decode(),
        "ct": base64.b64encode(ciphertext).decode(),
    }
    return base64.b64encode(json.dumps(envelope).encode()).decode()


def _parse_envelope(raw: bytes) -> dict[str, Any]:
    """Decode and shape-check an envelope.

    Anything else, a pre-FLIP#1179 CBC payload included, is a ``ValueError``: CBC ciphertext is
    indistinguishable from random bytes and never parses as a JSON object with these fields.
    """
    try:
        envelope = json.loads(raw)
    except ValueError:
        raise ValueError("Payload is not a FLIP encryption envelope") from None
    if not isinstance(envelope, dict) or not _ENVELOPE_FIELDS <= envelope.keys():
        raise ValueError("Payload is not a FLIP encryption envelope")
    if envelope["v"] != _VERSION:
        raise ValueError(f"Unsupported payload version: {envelope['v']!r}")
    return envelope


def decrypt(encoded_payload: str, key: bytes | None = None) -> str:
    """Decrypt an envelope produced by :func:`encrypt`.

    Args:
        encoded_payload (str): The base64-encoded envelope.
        key (bytes | None): Explicit key. If ``None``, resolved from the envelope's ``kid``.

    Returns:
        str: The decrypted plaintext.

    Raises:
        cryptography.exceptions.InvalidTag: The payload failed authentication: tampered ciphertext
            or nonce, wrong key, or altered ``kid``.
        KeyError: The envelope's ``kid`` names a key this service does not hold.
        ValueError: The payload is not a version-1 envelope (a pre-FLIP#1179 CBC payload lands here).
    """
    envelope = _parse_envelope(base64.b64decode(encoded_payload))
    kid = envelope["kid"]
    if key is None:
        key = _keyring().get(kid)
        if key is None:
            raise KeyError(f"No key registered for kid {kid!r}")

    nonce = base64.b64decode(envelope["iv"])
    ciphertext = base64.b64decode(envelope["ct"])
    return AESGCM(key).decrypt(nonce, ciphertext, _aad(kid)).decode()
