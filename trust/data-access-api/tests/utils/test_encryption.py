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

"""Tests for the authenticated (AES-GCM) payload envelope."""

import base64
import json
import os
from unittest.mock import patch

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from data_access_api.utils.encryption import SHARED_KID, decrypt, encrypt, get_aes_key

# Helper: generate valid 32-byte key (256-bit AES)
VALID_KEY = b"thisisaverysecurekey123456789012"  # 32 bytes
VALID_KEY_B64 = base64.b64encode(VALID_KEY).decode()
OTHER_KEY = os.urandom(32)


def _envelope(encoded: str) -> dict:
    return json.loads(base64.b64decode(encoded))


def _reencode(envelope: dict) -> str:
    return base64.b64encode(json.dumps(envelope).encode()).decode()


def _legacy_cbc(plaintext: str, key: bytes) -> str:
    """The pre-#1179 wire format: ``iv[16] || AES-CBC/PKCS7 ciphertext``, base64-encoded."""
    iv = os.urandom(16)
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext.encode()) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return base64.b64encode(iv + encryptor.update(padded) + encryptor.finalize()).decode()


# ---- key resolution ----


@patch("data_access_api.utils.encryption.get_settings")
def test_get_aes_key_valid(mock_get_settings):
    mock_get_settings.return_value.AES_KEY_BASE64 = VALID_KEY_B64
    assert get_aes_key() == VALID_KEY


@patch("data_access_api.utils.encryption.get_settings")
def test_get_aes_key_missing(mock_get_settings):
    mock_get_settings.return_value.AES_KEY_BASE64 = None
    with pytest.raises(ValueError, match="AES key not found in environment file"):
        get_aes_key()


@patch("data_access_api.utils.encryption.get_settings")
def test_get_aes_key_invalid_length(mock_get_settings):
    mock_get_settings.return_value.AES_KEY_BASE64 = base64.b64encode(b"shortkey").decode()
    with pytest.raises(ValueError, match="Invalid AES key length"):
        get_aes_key()


# ---- wire format ----


def test_envelope_is_versioned_json_under_the_shared_kid():
    envelope = _envelope(encrypt("payload", key=VALID_KEY))

    assert envelope["v"] == 1
    assert envelope["kid"] == SHARED_KID
    assert len(base64.b64decode(envelope["iv"])) == 12
    assert base64.b64decode(envelope["ct"])


def test_encrypt_output_is_base64():
    decoded = base64.b64decode(encrypt("some text", key=VALID_KEY))  # raises if not valid base64
    assert isinstance(decoded, bytes)


def test_nonce_is_fresh_per_call():
    assert _envelope(encrypt("same", key=VALID_KEY))["iv"] != _envelope(encrypt("same", key=VALID_KEY))["iv"]


# ---- round trips ----


@pytest.mark.parametrize(
    "plaintext", ["", "Sensitive data payload", '{"patient_id": 42, "name": "日本語"}', "x" * 10000]
)
def test_encrypt_decrypt_roundtrip(plaintext):
    assert decrypt(encrypt(plaintext, key=VALID_KEY), key=VALID_KEY) == plaintext


def test_encrypt_decrypt_roundtrip_with_None():
    assert decrypt(encrypt("Sensitive data payload", key=None), key=None) == "Sensitive data payload"


# ---- fails closed ----


def test_tampered_ciphertext_fails_closed():
    envelope = _envelope(encrypt("payload", key=VALID_KEY))
    ciphertext = bytearray(base64.b64decode(envelope["ct"]))
    ciphertext[0] ^= 0x01
    envelope["ct"] = base64.b64encode(bytes(ciphertext)).decode()

    with pytest.raises(InvalidTag):
        decrypt(_reencode(envelope), key=VALID_KEY)


def test_kid_is_bound_into_the_tag():
    envelope = _envelope(encrypt("payload", key=VALID_KEY))
    envelope["kid"] = "trust-other"

    with pytest.raises(InvalidTag):
        decrypt(_reencode(envelope), key=VALID_KEY)


def test_wrong_key_fails_closed():
    with pytest.raises(InvalidTag):
        decrypt(encrypt("payload", key=VALID_KEY), key=OTHER_KEY)


def test_unknown_kid_raises():
    envelope = _envelope(encrypt("payload"))
    envelope["kid"] = "trust-unknown"

    with pytest.raises(KeyError, match="trust-unknown"):
        decrypt(_reencode(envelope))


def test_unsupported_version_raises():
    envelope = _envelope(encrypt("payload", key=VALID_KEY))
    envelope["v"] = 2

    with pytest.raises(ValueError, match="version"):
        decrypt(_reencode(envelope), key=VALID_KEY)


def test_legacy_cbc_payload_is_rejected():
    """No CBC fallback: an unauthenticated pre-#1179 payload must not decrypt, even under the right key."""
    with pytest.raises(ValueError, match="not a FLIP encryption envelope"):
        decrypt(_legacy_cbc("payload", VALID_KEY), key=VALID_KEY)


def test_non_envelope_json_is_rejected():
    with pytest.raises(ValueError, match="not a FLIP encryption envelope"):
        decrypt(base64.b64encode(b'{"not": "an envelope"}').decode(), key=VALID_KEY)
