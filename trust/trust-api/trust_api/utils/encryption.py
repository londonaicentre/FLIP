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

"""AES-CBC decryption for task payloads received from the central hub."""

import base64

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from trust_api.config import get_settings

_aes_key_cache: bytes | None = None


def get_aes_key() -> bytes:
    """Retrieve the AES key from the environment and return it as bytes.

    Cached after first call — the key does not change during the lifetime of a process.
    """
    global _aes_key_cache  # noqa: PLW0603
    if _aes_key_cache is not None:
        return _aes_key_cache

    _aes_key_cache = base64.b64decode(get_settings().AES_KEY_BASE64)
    return _aes_key_cache


def decrypt(encoded_payload: str, key: bytes | None = None) -> str:
    """Decrypt Base64-encoded ciphertext using AES-CBC with PKCS7 padding."""
    if key is None:
        key = get_aes_key()

    encrypted_data = base64.b64decode(encoded_payload)
    iv = encrypted_data[:16]
    ciphertext = encrypted_data[16:]

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    decryptor = cipher.decryptor()
    padded_plaintext = decryptor.update(ciphertext) + decryptor.finalize()

    unpadder = padding.PKCS7(128).unpadder()
    plaintext = unpadder.update(padded_plaintext) + unpadder.finalize()
    return plaintext.decode()
