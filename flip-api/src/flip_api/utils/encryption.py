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
import os

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from flip_api.config import get_settings
from flip_api.utils.get_secrets import get_secret

_aes_key_cache: bytes | None = None


def get_aes_key() -> bytes:
    """Retrieve the AES key and return it as bytes.

    In production, fetches from AWS Secrets Manager. In dev, uses the environment variable directly.
    Cached after first call — the key does not change during the lifetime of a process.

    Returns:
        bytes: The decoded AES key.
    """
    global _aes_key_cache  # noqa: PLW0603
    if _aes_key_cache is not None:
        return _aes_key_cache

    stt = get_settings()
    aes_key_b64 = get_secret("aes_key") if stt.ENV == "production" else stt.AES_KEY_BASE64
    _aes_key_cache = base64.b64decode(aes_key_b64)
    return _aes_key_cache


def encrypt(plaintext: str, key: bytes | None = None) -> str:
    """Encrypt plaintext using AES-CBC with PKCS7 padding. Returns Base64-encoded ciphertext.

    Args:
        plaintext (str): The plaintext string to encrypt.
        key (bytes | None): The AES key to use. If None, the shared AES key is retrieved via
            :func:`get_aes_key`.

    Returns:
        str: Base64-encoded ciphertext, with the random IV prepended to the ciphertext bytes
        before encoding.
    """
    if key is None:
        key = get_aes_key()

    iv = os.urandom(16)

    padder = padding.PKCS7(128).padder()
    padded_data = padder.update(plaintext.encode()) + padder.finalize()

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded_data) + encryptor.finalize()

    return base64.b64encode(iv + ciphertext).decode()


def decrypt(encoded_payload: str, key: bytes | None = None) -> str:
    """Decrypt Base64-encoded ciphertext using AES-CBC with PKCS7 padding. Returns the original plaintext.

    Args:
        encoded_payload (str): Base64-encoded payload where the first 16 bytes are the IV and the
            remaining bytes are the ciphertext.
        key (bytes | None): The AES key to use. If None, the shared AES key is retrieved via
            :func:`get_aes_key`.

    Returns:
        str: The decrypted plaintext.
    """
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
