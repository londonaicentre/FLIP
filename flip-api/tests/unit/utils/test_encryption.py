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
import binascii
from unittest.mock import patch

import pytest

from flip_api.utils.encryption import decrypt, encrypt, get_aes_key

# Must be exactly 32 bytes (AES-256)
RAW_KEY_BYTES = b"ThisIsExactly32BytesLongKey!!!!1"
ENCODED_KEY = base64.b64encode(RAW_KEY_BYTES).decode()


def test_encryption_decryption_roundtrip():
    plaintext = "This is a test message"
    encrypted = encrypt(plaintext, RAW_KEY_BYTES)
    decrypted = decrypt(encrypted, RAW_KEY_BYTES)
    assert decrypted == plaintext


def test_get_aes_key_returns_decoded_bytes():
    with patch("flip_api.utils.encryption.get_secret", return_value=ENCODED_KEY):
        key = get_aes_key()
        assert key == RAW_KEY_BYTES


def test_get_aes_key_raises_if_secret_invalid_base64():
    with patch("flip_api.utils.encryption.get_secret", return_value="not-base64"):
        with pytest.raises(binascii.Error, match="Invalid base64-encoded string"):
            get_aes_key()


def test_invalid_key_length_raises():
    with pytest.raises(ValueError, match="Invalid key size"):
        encrypt("data", b"short")
