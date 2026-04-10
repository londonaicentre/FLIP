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

"""Tests for AES-CBC decryption utility."""

import base64
import os
from unittest.mock import patch

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

import trust_api.utils.encryption as encryption_module
from trust_api.utils.encryption import decrypt, get_aes_key


def _encrypt(plaintext: str, key: bytes) -> str:
    """Encrypt plaintext using AES-CBC with PKCS7 padding (test helper)."""
    iv = os.urandom(16)
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext.encode()) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(iv + ciphertext).decode()


class TestGetAesKey:
    def setup_method(self):
        encryption_module._aes_key_cache = None

    def test_returns_decoded_bytes(self):
        raw_key = os.urandom(32)
        b64_key = base64.b64encode(raw_key).decode()
        mock_settings = type("S", (), {"AES_KEY_BASE64": b64_key})()

        with patch("trust_api.utils.encryption.get_settings", return_value=mock_settings):
            result = get_aes_key()

        assert result == raw_key

    def test_caches_after_first_call(self):
        raw_key = os.urandom(32)
        b64_key = base64.b64encode(raw_key).decode()
        mock_settings = type("S", (), {"AES_KEY_BASE64": b64_key})()

        with patch("trust_api.utils.encryption.get_settings", return_value=mock_settings) as mock_get:
            first = get_aes_key()
            second = get_aes_key()

        assert first is second
        mock_get.assert_called_once()


class TestDecrypt:
    def test_decrypts_valid_payload(self):
        key = os.urandom(32)
        plaintext = "hello, trust!"
        encrypted = _encrypt(plaintext, key)

        result = decrypt(encrypted, key=key)

        assert result == plaintext

    def test_decrypts_empty_string(self):
        key = os.urandom(32)
        encrypted = _encrypt("", key)

        assert decrypt(encrypted, key=key) == ""

    def test_decrypts_unicode_content(self):
        key = os.urandom(32)
        plaintext = '{"patient_id": 42, "name": "Test"}'
        encrypted = _encrypt(plaintext, key)

        assert decrypt(encrypted, key=key) == plaintext

    def test_uses_get_aes_key_when_no_key_provided(self):
        key = os.urandom(32)
        plaintext = "auto-key test"
        encrypted = _encrypt(plaintext, key)

        with patch("trust_api.utils.encryption.get_aes_key", return_value=key):
            result = decrypt(encrypted)

        assert result == plaintext

    def test_decrypts_long_payload(self):
        key = os.urandom(32)
        plaintext = "x" * 10000
        encrypted = _encrypt(plaintext, key)

        assert decrypt(encrypted, key=key) == plaintext
