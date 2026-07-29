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

"""Authenticated encryption for cross-trust payloads (FLIP-PT-004).

AES-256-GCM (AEAD) in a small key-id'd envelope. Replaces the previous AES-CBC
scheme, which had no authentication: CBC ciphertexts were malleable, so a
tampered payload decrypted to attacker-influenced plaintext instead of failing.
GCM authenticates the ciphertext and the envelope's ``kid``, so any tampering
raises ``InvalidTag``.

Every ciphertext carries a **key id** (``kid``) and every service resolves keys
through a small keyring. That is what makes key rotation and **per-trust keys**
possible: receivers can hold several keys at once, so a key can be introduced or
retired without every participant changing over at the same moment.

Wire format — base64 of ``{"v": 1, "kid": ..., "iv": ..., "ct": ...}``, where
``iv`` is a 12-byte GCM nonce and ``ct`` is ciphertext‖tag. ``v`` is a format
discriminator so the envelope itself can be changed later without ambiguity;
the algorithm is fixed (AES-256-GCM) rather than negotiated, which removes any
algorithm-confusion surface.

Keys (all optional except the shared key):

- ``AES_KEY_BASE64`` — the shared key, always registered under
  :data:`SHARED_KID`. Used when nothing else is configured.
- ``TRUST_AES_KID`` + ``TRUST_AES_KEY_BASE64`` — a trust's own key. When set,
  that key is used for anything this service encrypts.
- ``AES_TRUST_KEYS`` — hub-side JSON map ``{kid: base64_key}`` of per-trust keys,
  so the hub can encrypt to (and decrypt from) a specific trust by ``kid``.

.. rubric:: Temporary compatibility shim — DELETE AFTER ROLL-OUT

:func:`decrypt` also accepts the previous **AES-CBC** payload format, so a hub and
a trust running different builds can still talk to each other while every host is
upgraded (on-prem trusts cannot all be restarted at once). Nothing ever *writes*
CBC — :func:`encrypt` is GCM-only — so no new unauthenticated ciphertext is
created, and no payload is stored at rest in either format.

Once every hub and trust runs this build: set ``AES_ACCEPT_LEGACY_CBC=false`` to
verify nothing still sends CBC, then delete :func:`_decrypt_legacy_cbc`,
:func:`_accept_legacy_cbc`, the fallback branch in :func:`decrypt`, and the CBC
imports below.
"""

import base64
import json
import os

from cryptography.hazmat.primitives import padding  # legacy-CBC shim
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # legacy-CBC shim
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from imaging_api.config import get_settings

#: Key-id under which the shared ``AES_KEY_BASE64`` is registered.
SHARED_KID = "shared"

_VERSION = 1
_NONCE_BYTES = 12  # NIST SP 800-38D recommended nonce length for AES-GCM

_keyring_cache: dict[str, bytes] | None = None


def _shared_key() -> bytes:
    """Return the shared AES key from settings (``AES_KEY_BASE64``)."""
    return base64.b64decode(get_settings().AES_KEY_BASE64)


def _keyring() -> dict[str, bytes]:
    """Return the ``kid -> key`` keyring: the shared key plus any configured extras.

    Cached — keys do not change during a process lifetime.
    """
    global _keyring_cache  # noqa: PLW0603
    if _keyring_cache is not None:
        return _keyring_cache

    ring: dict[str, bytes] = {SHARED_KID: _shared_key()}

    own_kid, own_key = os.environ.get("TRUST_AES_KID"), os.environ.get("TRUST_AES_KEY_BASE64")
    if own_kid and own_key:
        ring[own_kid] = base64.b64decode(own_key)

    trust_keys = os.environ.get("AES_TRUST_KEYS")
    if trust_keys:
        ring.update({kid: base64.b64decode(key) for kid, key in json.loads(trust_keys).items()})

    _keyring_cache = ring
    return ring


def _default_kid() -> str:
    """Return the kid used when the caller does not name one.

    A service configured with its own key (``TRUST_AES_KID``) uses it; otherwise
    the shared key.
    """
    return os.environ.get("TRUST_AES_KID") or SHARED_KID


def _reset_caches() -> None:
    """Clear the keyring cache. Test-only helper."""
    global _keyring_cache  # noqa: PLW0603
    _keyring_cache = None


def _aad(kid: str) -> bytes:
    """Associated data bound into the GCM tag, so the ``kid`` cannot be swapped."""
    return f"FLIP|v{_VERSION}|{kid}".encode()


def encrypt(plaintext: str, key: bytes | None = None, kid: str | None = None) -> str:
    """Encrypt ``plaintext`` with AES-256-GCM.

    Args:
        plaintext (str): The text to encrypt.
        key (bytes | None): Explicit key. If ``None``, resolved from the keyring
            by ``kid``.
        kid (str | None): Key id. Selects the key when ``key`` is ``None``
            (default: :func:`_default_kid`), and is recorded in the envelope.

    Returns:
        str: Base64-encoded envelope.

    Raises:
        KeyError: ``key`` is ``None`` and ``kid`` is not in the keyring.
        ValueError: The key is not a valid AES key length.
    """
    if key is None:
        kid = kid or _default_kid()
        key = _keyring()[kid]
    else:
        kid = kid or SHARED_KID

    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode(), _aad(kid))
    envelope = {
        "v": _VERSION,
        "kid": kid,
        "iv": base64.b64encode(nonce).decode(),
        "ct": base64.b64encode(ciphertext).decode(),
    }
    return base64.b64encode(json.dumps(envelope).encode()).decode()


def _accept_legacy_cbc() -> bool:
    """Whether pre-GCM AES-CBC payloads are still accepted. SHIM — delete after roll-out."""
    return os.environ.get("AES_ACCEPT_LEGACY_CBC", "true").strip().lower() not in ("false", "0", "no")


def _decrypt_legacy_cbc(raw: bytes, key: bytes | None) -> str:
    """Decrypt a pre-GCM ``iv[16] ‖ ciphertext`` AES-CBC payload.

    SHIM — delete after roll-out. Unauthenticated by construction: this is only
    here so a not-yet-upgraded peer can still be understood during the upgrade.

    Raises:
        ValueError: When ``AES_ACCEPT_LEGACY_CBC`` is disabled.
    """
    if not _accept_legacy_cbc():
        raise ValueError("Legacy AES-CBC payload rejected (AES_ACCEPT_LEGACY_CBC is disabled)")
    if key is None:
        key = _keyring()[SHARED_KID]

    decryptor = Cipher(algorithms.AES(key), modes.CBC(raw[:16])).decryptor()
    padded_plaintext = decryptor.update(raw[16:]) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return (unpadder.update(padded_plaintext) + unpadder.finalize()).decode()


def _parse_envelope(raw: bytes) -> dict | None:
    """Return the decoded GCM envelope, or ``None`` if these are not envelope bytes.

    CBC ciphertext is indistinguishable from random, so it never parses as a JSON
    object carrying ``kid`` — which makes this a safe way to tell the two formats
    apart without putting a marker in the envelope.
    """
    try:
        envelope = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return envelope if isinstance(envelope, dict) and "kid" in envelope else None


def decrypt(encoded_payload: str, key: bytes | None = None) -> str:
    """Decrypt a payload produced by :func:`encrypt`.

    Also accepts the pre-GCM AES-CBC format while the estate is being upgraded —
    see the module docstring's shim note.

    Args:
        encoded_payload (str): The base64-encoded envelope.
        key (bytes | None): Explicit key. If ``None``, resolved from the keyring
            by the envelope's ``kid``.

    Returns:
        str: The decrypted plaintext.

    Raises:
        cryptography.exceptions.InvalidTag: The payload failed authentication
            (tampered ciphertext, wrong key, or altered ``kid``).
        KeyError: The envelope's ``kid`` is not in the keyring.
        ValueError: The envelope is malformed, an unsupported version, or a legacy
            CBC payload arrived while ``AES_ACCEPT_LEGACY_CBC`` is disabled.
    """
    raw = base64.b64decode(encoded_payload)

    envelope = _parse_envelope(raw)
    if envelope is None:
        return _decrypt_legacy_cbc(raw, key)  # SHIM — delete after roll-out

    if envelope.get("v") != _VERSION:
        raise ValueError(f"Unsupported payload version: {envelope.get('v')!r}")

    kid = envelope["kid"]
    if key is None:
        key = _keyring().get(kid)
        if key is None:
            raise KeyError(f"No key registered for kid {kid!r}")

    nonce = base64.b64decode(envelope["iv"])
    ciphertext = base64.b64decode(envelope["ct"])
    return AESGCM(key).decrypt(nonce, ciphertext, _aad(kid)).decode()
