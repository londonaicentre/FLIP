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

import base64
import json

import pytest
from cryptography.exceptions import InvalidTag

from imaging_api.utils import encryption
from imaging_api.utils.encryption import SHARED_KID, _reset_caches, decrypt, encrypt

RAW_KEY_BYTES = b"ThisIsExactly32BytesLongKey!!!!1"
ENCODED_KEY = base64.b64encode(RAW_KEY_BYTES).decode()
TRUST_KEY_B64 = base64.b64encode(b"AnotherExactly32ByteAESKey!!!!!2").decode()


@pytest.fixture(autouse=True)
def _shared_key_settings(monkeypatch):
    monkeypatch.setattr(encryption, "get_settings", lambda: type("S", (), {"AES_KEY_BASE64": ENCODED_KEY})())
    for var in ("AES_TRUST_KEYS", "TRUST_AES_KID", "TRUST_AES_KEY_BASE64"):
        monkeypatch.delenv(var, raising=False)
    _reset_caches()
    yield
    _reset_caches()


def _envelope(payload: str) -> dict:
    return json.loads(base64.b64decode(payload))


def _reseal(envelope: dict) -> str:
    return base64.b64encode(json.dumps(envelope).encode()).decode()


def test_roundtrip_via_keyring():
    assert decrypt(encrypt("hello")) == "hello"


def test_roundtrip_explicit_key():
    assert decrypt(encrypt("secret", key=RAW_KEY_BYTES), key=RAW_KEY_BYTES) == "secret"


def test_default_kid_is_shared():
    assert _envelope(encrypt("x"))["kid"] == SHARED_KID


def test_tampered_ciphertext_fails_closed():
    envelope = _envelope(encrypt("do not tamper"))
    raw = bytearray(base64.b64decode(envelope["ct"]))
    raw[0] ^= 0x01
    envelope["ct"] = base64.b64encode(bytes(raw)).decode()
    with pytest.raises(InvalidTag):
        decrypt(_reseal(envelope))


def test_kid_swap_fails_closed():
    envelope = _envelope(encrypt("bound", key=RAW_KEY_BYTES, kid="kid-a"))
    envelope["kid"] = "kid-b"
    with pytest.raises(InvalidTag):
        decrypt(_reseal(envelope), key=RAW_KEY_BYTES)


def test_unsupported_version_raises():
    envelope = _envelope(encrypt("x"))
    envelope["v"] = 99
    with pytest.raises(ValueError, match="Unsupported payload version"):
        decrypt(_reseal(envelope))


def test_own_trust_key_is_used_by_default(monkeypatch):
    monkeypatch.setenv("TRUST_AES_KID", "trust-GSTT")
    monkeypatch.setenv("TRUST_AES_KEY_BASE64", TRUST_KEY_B64)
    _reset_caches()
    payload = encrypt("per-trust")
    assert _envelope(payload)["kid"] == "trust-GSTT"
    assert decrypt(payload) == "per-trust"


def test_decrypts_payload_addressed_to_this_trust(monkeypatch):
    """Hub encrypts to this trust's kid; the trust decrypts it with its own key."""
    monkeypatch.setenv("TRUST_AES_KID", "trust-GSTT")
    monkeypatch.setenv("TRUST_AES_KEY_BASE64", TRUST_KEY_B64)
    _reset_caches()
    from_hub = encrypt("task payload", key=base64.b64decode(TRUST_KEY_B64), kid="trust-GSTT")
    assert decrypt(from_hub) == "task payload"


# --- temporary AES-CBC compatibility shim (delete with the shim) ---------------


def _legacy_cbc_payload(plaintext: str, key: bytes = RAW_KEY_BYTES) -> str:
    """Produce a payload in the pre-GCM format, exactly as an un-upgraded peer would."""
    import os

    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    iv = os.urandom(16)
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext.encode()) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return base64.b64encode(iv + encryptor.update(padded) + encryptor.finalize()).decode()


def test_legacy_cbc_from_unupgraded_hub_still_decrypts():
    assert decrypt(_legacy_cbc_payload("task payload")) == "task payload"


def test_legacy_cbc_rejected_once_disabled(monkeypatch):
    monkeypatch.setenv("AES_ACCEPT_LEGACY_CBC", "false")
    _reset_caches()
    with pytest.raises(ValueError, match="Legacy AES-CBC payload rejected"):
        decrypt(_legacy_cbc_payload("x"))


def test_gcm_still_works_when_legacy_disabled(monkeypatch):
    """Turning the shim off must not affect the real format."""
    monkeypatch.setenv("AES_ACCEPT_LEGACY_CBC", "false")
    _reset_caches()
    assert decrypt(encrypt("still fine")) == "still fine"
