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
from unittest.mock import MagicMock, patch

import pytest

from imaging_api.utils.encryption import decrypt, encrypt, get_aes_key


class TestGetAesKey:
    def test_valid_key(self):
        key = get_aes_key()
        assert isinstance(key, bytes)
        assert len(key) in (16, 24, 32)

    @patch("imaging_api.utils.encryption.get_settings")
    def test_missing_key_raises_value_error(self, mock_settings):
        mock_settings.return_value = MagicMock(AES_KEY_BASE64="")
        with pytest.raises(ValueError, match="AES key not found"):
            get_aes_key()

    @patch("imaging_api.utils.encryption.get_settings")
    def test_invalid_key_length_raises_value_error(self, mock_settings):
        # 10 bytes → invalid AES key length
        mock_settings.return_value = MagicMock(AES_KEY_BASE64=base64.b64encode(b"x" * 10).decode())
        with pytest.raises(ValueError, match="Invalid AES key length"):
            get_aes_key()


class TestEncryptDecrypt:
    def test_roundtrip(self):
        plaintext = "hello-project-id-12345"
        key = get_aes_key()
        encrypted = encrypt(plaintext, key)
        decrypted = decrypt(encrypted, key)
        assert decrypted == plaintext

    def test_encrypt_produces_different_ciphertexts(self):
        plaintext = "same-text"
        key = get_aes_key()
        c1 = encrypt(plaintext, key)
        c2 = encrypt(plaintext, key)
        assert c1 != c2  # random IV → different ciphertexts

    def test_encrypt_uses_default_key(self):
        plaintext = "auto-key-test"
        encrypted = encrypt(plaintext)
        decrypted = decrypt(encrypted)
        assert decrypted == plaintext

    def test_roundtrip_with_unicode(self):
        plaintext = "patient-name-日本��"
        key = get_aes_key()
        assert decrypt(encrypt(plaintext, key), key) == plaintext

    def test_roundtrip_with_long_text(self):
        plaintext = "a" * 1000
        key = get_aes_key()
        assert decrypt(encrypt(plaintext, key), key) == plaintext
