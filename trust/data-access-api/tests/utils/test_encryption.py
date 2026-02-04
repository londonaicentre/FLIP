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
from unittest.mock import patch

import pytest

from data_access_api.utils.encryption import decrypt, encrypt, get_aes_key

# Helper: generate valid 32-byte key (256-bit AES)
VALID_KEY = b"thisisaverysecurekey123456789012"  # 32 bytes
VALID_KEY_B64 = base64.b64encode(VALID_KEY).decode()


@patch("data_access_api.utils.encryption.get_settings")
def test_get_aes_key_valid(mock_get_settings):
    mock_get_settings.return_value.AES_KEY_BASE64 = VALID_KEY_B64
    key = get_aes_key()
    assert key == VALID_KEY


@patch("data_access_api.utils.encryption.get_settings")
def test_get_aes_key_missing(mock_get_settings):
    mock_get_settings.return_value.AES_KEY_BASE64 = None
    with pytest.raises(ValueError, match="AES key not found in environment file"):
        get_aes_key()


@patch("data_access_api.utils.encryption.get_settings")
def test_get_aes_key_invalid_length(mock_get_settings):
    short_key = base64.b64encode(b"shortkey").decode()
    mock_get_settings.return_value.AES_KEY_BASE64 = short_key
    with pytest.raises(ValueError, match="Invalid AES key length"):
        get_aes_key()


def test_encrypt_decrypt_roundtrip():
    plaintext = "Sensitive data payload"
    encrypted = encrypt(plaintext, key=VALID_KEY)
    decrypted = decrypt(encrypted, key=VALID_KEY)
    assert decrypted == plaintext


def test_encrypt_decrypt_roundtrip_with_None():
    plaintext = "Sensitive data payload"
    encrypted = encrypt(plaintext, key=None)
    decrypted = decrypt(encrypted, key=None)
    assert decrypted == plaintext


def test_encrypt_output_is_base64():
    encrypted = encrypt("some text", key=VALID_KEY)
    # This will raise if it's not valid base64
    decoded = base64.b64decode(encrypted)
    assert isinstance(decoded, bytes)
    assert len(decoded) > 16  # IV + ciphertext
