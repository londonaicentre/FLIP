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
ciphertext together with the envelope's key id and the caller's *context* string, so an
altered ciphertext, nonce, key id or context raises ``InvalidTag`` instead of decrypting
to attacker-influenced plaintext, which is what the previous AES-CBC scheme did (it had
no authentication, and a CBC ciphertext is malleable). An altered version is rejected
earlier, as an unsupported envelope.

Wire format: base64 of ``{"v": 1, "kid": "shared", "iv": <b64 nonce>, "ct": <b64 ciphertext||tag>}``,
with a 12-byte nonce. ``v`` is a format discriminator so the envelope can change later
without ambiguity; the algorithm is fixed rather than negotiated, which leaves no
algorithm-confusion surface. ``kid`` names the key the payload was encrypted under.
Every service holds the platform-wide ``AES_KEY_BASE64``, kept in the keyring under
:data:`SHARED_KID`; the keyring is the extension point for per-trust keys (FLIP#845),
which add entries and change nothing on the wire.

The **context** is not carried in the envelope: both sides derive it from what they
already know (``task:<task_type>`` for a task payload, ``project_id`` for the project id
handed to FL clients, ``xnat_password`` for the credential a trust returns). It is bound
into the authentication tag, so a payload sealed for one purpose does not verify when
presented for another — a task payload cannot be re-targeted at a different handler by
rewriting the unauthenticated ``task_type`` beside it.

There is deliberately no fallback to the pre-FLIP#1179 CBC format: an unauthenticated
ciphertext is never accepted, so a peer on either side of that change cannot exchange
payloads with a peer on the other (the roll-out is a flag day; see ``deploy/README.md``).
"""

import base64
import binascii
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
_NOT_AN_ENVELOPE = "Payload is not a FLIP encryption envelope"


class UnknownKeyIdError(KeyError):
    """The envelope names a key id this service does not hold.

    A ``KeyError`` so existing ``except KeyError`` handling keeps working, with a plain
    message (``KeyError`` would wrap it in quotes).
    """

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else ""


def _keyring() -> dict[str, bytes]:
    """Return the ``kid -> key`` map payloads are encrypted under and resolved against.

    One entry. Per-trust keys (FLIP#845) are added here, which is why :func:`decrypt`
    resolves the key from the envelope's ``kid`` rather than assuming the shared one.
    """
    return {SHARED_KID: get_aes_key()}


def _aad(kid: str, context: str) -> bytes:
    """Associated data bound into the GCM tag: the format version, the key id and the caller's context."""
    return f"FLIP|v{_VERSION}|{kid}|{context}".encode()


def encrypt(plaintext: str, key: bytes | None = None, *, context: str = "") -> str:
    """Encrypt ``plaintext`` with AES-256-GCM under the shared key.

    Args:
        plaintext (str): The text to encrypt.
        key (bytes | None): Explicit key, for tests. It overrides the key but not the label:
            the envelope always says ``kid="shared"``, so a payload under any other key is
            unreadable by a peer resolving the key from its keyring.
        context (str): Purpose label bound into the authentication tag (see the module
            docstring). The receiver must pass the same value to :func:`decrypt`.

    Returns:
        str: Base64-encoded envelope (see the module docstring for the format).

    Raises:
        ValueError: The key is not a valid AES key length.
    """
    kid = SHARED_KID
    if key is None:
        key = _keyring()[kid]

    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode(), _aad(kid, context))
    envelope = {
        "v": _VERSION,
        "kid": kid,
        "iv": base64.b64encode(nonce).decode(),
        "ct": base64.b64encode(ciphertext).decode(),
    }
    return base64.b64encode(json.dumps(envelope).encode()).decode()


def _b64decode(value: str) -> bytes:
    """Strict base64 decode; anything that is not base64 is not an envelope."""
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError, TypeError):
        raise ValueError(_NOT_AN_ENVELOPE) from None


def _parse_envelope(raw: bytes) -> dict[str, Any]:
    """Decode and check an envelope's shape and field types.

    Anything else, a pre-FLIP#1179 CBC payload included, is a ``ValueError``: CBC ciphertext is
    indistinguishable from random bytes and never parses as a JSON object with these fields.
    Types are checked as well as presence, so a malformed envelope never surfaces as a
    ``TypeError`` from the decoder or the keyring lookup.
    """
    try:
        envelope = json.loads(raw)
    except ValueError:
        raise ValueError(_NOT_AN_ENVELOPE) from None
    if not isinstance(envelope, dict) or not _ENVELOPE_FIELDS <= envelope.keys():
        raise ValueError(_NOT_AN_ENVELOPE)
    version = envelope["v"]
    if not isinstance(version, int) or isinstance(version, bool):
        raise ValueError(_NOT_AN_ENVELOPE)
    if not all(isinstance(envelope[field], str) for field in ("kid", "iv", "ct")):
        raise ValueError(_NOT_AN_ENVELOPE)
    if version != _VERSION:
        raise ValueError(f"Unsupported payload version: {version!r}")
    return envelope


def decrypt(encoded_payload: str, key: bytes | None = None, *, context: str = "") -> str:
    """Decrypt an envelope produced by :func:`encrypt`.

    Args:
        encoded_payload (str): The base64-encoded envelope.
        key (bytes | None): Explicit key. If ``None``, resolved from the envelope's ``kid``.
        context (str): The purpose label the sender sealed the payload with.

    Returns:
        str: The decrypted plaintext.

    Raises:
        cryptography.exceptions.InvalidTag: The payload failed authentication: tampered ciphertext
            or nonce, wrong key, altered ``kid``, or a ``context`` other than the sender's.
        UnknownKeyIdError: The envelope's ``kid`` names a key this service does not hold
            (a ``KeyError``).
        ValueError: The payload is not a well-formed version-1 envelope: not base64, not JSON,
            missing or wrongly typed fields, another version (a pre-FLIP#1179 CBC payload lands
            here), or a nonce of an invalid length.
    """
    envelope = _parse_envelope(_b64decode(encoded_payload))
    kid = envelope["kid"]
    if key is None:
        key = _keyring().get(kid)
        if key is None:
            raise UnknownKeyIdError(f"No key registered for kid {kid!r}")

    nonce = _b64decode(envelope["iv"])
    ciphertext = _b64decode(envelope["ct"])
    return AESGCM(key).decrypt(nonce, ciphertext, _aad(kid, context)).decode()
