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
from typing import Optional

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from data_access_api.config import get_settings


# --- Step 1: Load AES key from environment file ---
def get_aes_key() -> bytes:
    key_b64 = get_settings().AES_KEY_BASE64
    if not key_b64:
        raise ValueError("AES key not found in environment file")

    key = base64.b64decode(key_b64)
    if len(key) not in (16, 24, 32):
        raise ValueError("Invalid AES key length")
    return key


# --- Step 2: AES-CBC encryption ---
def encrypt(plaintext: str, key: Optional[bytes] = None) -> str:
    if key is None:
        key = get_aes_key()

    iv = os.urandom(16)

    # Pad plaintext to 128-bit (16-byte) blocks
    padder = padding.PKCS7(128).padder()
    padded_data = padder.update(plaintext.encode()) + padder.finalize()

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded_data) + encryptor.finalize()

    # Combine IV and ciphertext, then encode for storage/transmission
    encrypted_payload = iv + ciphertext
    return base64.b64encode(encrypted_payload).decode()


# --- Step 3: AES-CBC decryption ---
def decrypt(encoded_payload: str, key: Optional[bytes] = None) -> str:
    if key is None:
        key = get_aes_key()

    encrypted_data = base64.b64decode(encoded_payload)
    iv = encrypted_data[:16]
    ciphertext = encrypted_data[16:]

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    decryptor = cipher.decryptor()
    padded_plaintext = decryptor.update(ciphertext) + decryptor.finalize()

    # Unpad to recover original plaintext
    unpadder = padding.PKCS7(128).unpadder()
    plaintext = unpadder.update(padded_plaintext) + unpadder.finalize()
    return plaintext.decode()
