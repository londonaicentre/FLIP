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
from unittest.mock import MagicMock, patch

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from imaging_api.utils.encryption import SHARED_KID, decrypt, encrypt, get_aes_key

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


class TestWireFormat:
    def test_envelope_is_versioned_json_under_the_shared_kid(self):
        envelope = _envelope(encrypt("payload", get_aes_key()))

        assert envelope["v"] == 1
        assert envelope["kid"] == SHARED_KID
        assert len(base64.b64decode(envelope["iv"])) == 12
        assert base64.b64decode(envelope["ct"])

    def test_encrypt_produces_different_ciphertexts(self):
        key = get_aes_key()
        assert encrypt("same-text", key) != encrypt("same-text", key)  # fresh nonce per call


class TestEncryptDecrypt:
    @pytest.mark.parametrize("plaintext", ["", "hello-project-id-12345", "patient-name-日本語", "a" * 1000])
    def test_roundtrip(self, plaintext):
        key = get_aes_key()
        assert decrypt(encrypt(plaintext, key), key) == plaintext

    def test_encrypt_uses_default_key(self):
        assert decrypt(encrypt("auto-key-test")) == "auto-key-test"

    def test_tampered_ciphertext_fails_closed(self):
        key = get_aes_key()
        envelope = _envelope(encrypt("payload", key))
        ciphertext = bytearray(base64.b64decode(envelope["ct"]))
        ciphertext[0] ^= 0x01
        envelope["ct"] = base64.b64encode(bytes(ciphertext)).decode()

        with pytest.raises(InvalidTag):
            decrypt(_reencode(envelope), key)

    def test_kid_is_bound_into_the_tag(self):
        key = get_aes_key()
        envelope = _envelope(encrypt("payload", key))
        envelope["kid"] = "trust-other"

        with pytest.raises(InvalidTag):
            decrypt(_reencode(envelope), key)

    def test_wrong_key_fails_closed(self):
        with pytest.raises(InvalidTag):
            decrypt(encrypt("payload", get_aes_key()), OTHER_KEY)

    def test_unknown_kid_raises(self):
        envelope = _envelope(encrypt("payload"))
        envelope["kid"] = "trust-unknown"

        with pytest.raises(KeyError, match="trust-unknown"):
            decrypt(_reencode(envelope))

    def test_unsupported_version_raises(self):
        key = get_aes_key()
        envelope = _envelope(encrypt("payload", key))
        envelope["v"] = 2

        with pytest.raises(ValueError, match="version"):
            decrypt(_reencode(envelope), key)

    def test_legacy_cbc_payload_is_rejected(self):
        """No CBC fallback: an unauthenticated pre-#1179 payload must not decrypt, even under the right key."""
        key = get_aes_key()
        with pytest.raises(ValueError, match="not a FLIP encryption envelope"):
            decrypt(_legacy_cbc("payload", key), key)

    def test_non_envelope_json_is_rejected(self):
        with pytest.raises(ValueError, match="not a FLIP encryption envelope"):
            decrypt(base64.b64encode(b'{"not": "an envelope"}').decode(), get_aes_key())


# ---- cross-service contract: the same vector must decrypt in every service ----

KAT_KEY = bytes.fromhex("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f")
KAT_CONTEXT = "kat"
KAT_PLAINTEXT = "known-answer"
#: encrypt(KAT_PLAINTEXT, KAT_KEY, context=KAT_CONTEXT) with nonce 0x00..0x0b. Identical in all four services'
#: test suites, so a copy that drifts from the others fails here instead of in production.
KAT_ENVELOPE = (
    "eyJ2IjogMSwgImtpZCI6ICJzaGFyZWQiLCAiaXYiOiAiQUFFQ0F3UUZCZ2NJQ1FvTCIsICJjdCI6ICJMR3k1Ykt2SW8zWCtOdkw1MFo3MFNwSTgz"  # pragma: allowlist secret
    "d1NLbGlyaUxJa25PQT09In0="
)


def test_known_answer_vector_decrypts():
    assert decrypt(KAT_ENVELOPE, KAT_KEY, context=KAT_CONTEXT) == KAT_PLAINTEXT


def test_context_is_bound_into_the_tag():
    """A payload authenticated for one purpose must not verify when presented for another."""
    sealed = encrypt("payload", KAT_KEY, context="task:get_imaging_status")

    with pytest.raises(InvalidTag):
        decrypt(sealed, KAT_KEY, context="task:delete_imaging")


def test_context_defaults_to_empty_on_both_sides():
    assert decrypt(encrypt("payload", KAT_KEY), KAT_KEY) == "payload"


def test_tampered_nonce_fails_closed():
    envelope = _envelope(encrypt("payload", KAT_KEY))
    nonce = bytearray(base64.b64decode(envelope["iv"]))
    nonce[0] ^= 0x01
    envelope["iv"] = base64.b64encode(bytes(nonce)).decode()

    with pytest.raises(InvalidTag):
        decrypt(_reencode(envelope), KAT_KEY)


@pytest.mark.parametrize("raw", [b"[1, 2, 3]", b'"envelope"', b"42", b"null"])
def test_non_dict_json_is_rejected(raw):
    with pytest.raises(ValueError, match="not a FLIP encryption envelope"):
        decrypt(base64.b64encode(raw).decode(), KAT_KEY)


@pytest.mark.parametrize(("field", "value"), [("kid", ["shared"]), ("iv", 5), ("ct", None), ("v", True)])
def test_wrong_field_types_are_rejected(field, value):
    """Shape alone is not enough: every field must also be the right type, or callers see a TypeError."""
    envelope = _envelope(encrypt("payload", KAT_KEY))
    envelope[field] = value

    with pytest.raises(ValueError, match="not a FLIP encryption envelope"):
        decrypt(_reencode(envelope), KAT_KEY)


@pytest.mark.parametrize("payload", ["not base64!!", "abc"])
def test_invalid_base64_payload_is_rejected(payload):
    with pytest.raises(ValueError, match="not a FLIP encryption envelope"):
        decrypt(payload, KAT_KEY)


def test_invalid_base64_inside_envelope_is_rejected():
    envelope = _envelope(encrypt("payload", KAT_KEY))
    envelope["iv"] = "!!!"

    with pytest.raises(ValueError, match="not a FLIP encryption envelope"):
        decrypt(_reencode(envelope), KAT_KEY)
