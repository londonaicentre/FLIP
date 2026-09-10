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

import trust_api.utils.encryption as encryption_module
from trust_api.utils.encryption import SHARED_KID, decrypt, encrypt, get_aes_key

KEY = os.urandom(32)
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


class TestGetAesKey:
    def setup_method(self):
        encryption_module._aes_key_cache = None

    def test_returns_decoded_bytes(self):
        mock_settings = type("S", (), {"AES_KEY_BASE64": base64.b64encode(KEY).decode()})()

        with patch("trust_api.utils.encryption.get_settings", return_value=mock_settings):
            assert get_aes_key() == KEY

    def test_caches_after_first_call(self):
        mock_settings = type("S", (), {"AES_KEY_BASE64": base64.b64encode(KEY).decode()})()

        with patch("trust_api.utils.encryption.get_settings", return_value=mock_settings) as mock_get:
            first = get_aes_key()
            second = get_aes_key()

        assert first is second
        mock_get.assert_called_once()


class TestWireFormat:
    def test_envelope_is_versioned_json_under_the_shared_kid(self):
        envelope = _envelope(encrypt("payload", KEY))

        assert envelope["v"] == 1
        assert envelope["kid"] == SHARED_KID
        assert len(base64.b64decode(envelope["iv"])) == 12
        assert base64.b64decode(envelope["ct"])

    def test_nonce_is_fresh_per_call(self):
        assert _envelope(encrypt("same", KEY))["iv"] != _envelope(encrypt("same", KEY))["iv"]


class TestDecrypt:
    @pytest.mark.parametrize("plaintext", ["", "hello, trust!", '{"patient_id": 42, "name": "日本語"}', "x" * 10000])
    def test_roundtrip_with_explicit_key(self, plaintext):
        assert decrypt(encrypt(plaintext, KEY), KEY) == plaintext

    def test_uses_get_aes_key_when_no_key_provided(self):
        with patch("trust_api.utils.encryption.get_aes_key", return_value=KEY):
            assert decrypt(encrypt("auto-key test")) == "auto-key test"

    def test_tampered_ciphertext_fails_closed(self):
        envelope = _envelope(encrypt("payload", KEY))
        ciphertext = bytearray(base64.b64decode(envelope["ct"]))
        ciphertext[0] ^= 0x01
        envelope["ct"] = base64.b64encode(bytes(ciphertext)).decode()

        with pytest.raises(InvalidTag):
            decrypt(_reencode(envelope), KEY)

    def test_kid_is_bound_into_the_tag(self):
        envelope = _envelope(encrypt("payload", KEY))
        envelope["kid"] = "trust-other"

        with pytest.raises(InvalidTag):
            decrypt(_reencode(envelope), KEY)

    def test_wrong_key_fails_closed(self):
        with pytest.raises(InvalidTag):
            decrypt(encrypt("payload", KEY), OTHER_KEY)

    def test_unknown_kid_raises(self):
        envelope = _envelope(encrypt("payload", KEY))
        envelope["kid"] = "trust-unknown"

        with patch("trust_api.utils.encryption.get_aes_key", return_value=KEY):
            with pytest.raises(KeyError, match="trust-unknown"):
                decrypt(_reencode(envelope))

    def test_unsupported_version_raises(self):
        envelope = _envelope(encrypt("payload", KEY))
        envelope["v"] = 2

        with pytest.raises(ValueError, match="version"):
            decrypt(_reencode(envelope), KEY)

    def test_legacy_cbc_payload_is_rejected(self):
        """No CBC fallback: an unauthenticated pre-#1179 payload must not decrypt, even under the right key."""
        with pytest.raises(ValueError, match="not a FLIP encryption envelope"):
            decrypt(_legacy_cbc("payload", KEY), KEY)

    def test_non_envelope_json_is_rejected(self):
        with pytest.raises(ValueError, match="not a FLIP encryption envelope"):
            decrypt(base64.b64encode(b'{"not": "an envelope"}').decode(), KEY)
