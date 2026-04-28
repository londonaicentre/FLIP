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
import datetime
from unittest.mock import MagicMock, patch

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509 import CertificateBuilder, Name, NameAttribute, random_serial_number
from cryptography.x509.oid import NameOID

from flip_api.domain.schemas.file import SNSNotification
from flip_api.utils.sns import (
    SNSVerificationError,
    _canonical_string,
    confirm_subscription,
    verify_sns_signature,
)


@pytest.fixture
def rsa_keypair_and_cert():
    """Generate a self-signed RSA cert we can use to sign synthetic SNS payloads."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = Name([NameAttribute(NameOID.COMMON_NAME, "sns.eu-west-2.amazonaws.com")])
    cert = (
        CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(random_serial_number())
        .not_valid_before(datetime.datetime.utcnow() - datetime.timedelta(days=1))
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    return key, cert.public_bytes(serialization.Encoding.PEM)


def _signed_payload(key, cert_url: str, *, message_type: str = "Notification") -> SNSNotification:
    payload_dict = {
        "Type": message_type,
        "MessageId": "abc",
        "TopicArn": "arn:aws:sns:eu-west-2:111111111111:test",
        "Subject": "S",
        "Message": "hello",
        "Timestamp": "2026-01-01T00:00:00.000Z",
        "SignatureVersion": "1",
        "Signature": "",
        "SigningCertURL": cert_url,
    }
    if message_type == "SubscriptionConfirmation":
        payload_dict["Token"] = "tok"
        payload_dict["SubscribeURL"] = "https://sns.eu-west-2.amazonaws.com/?Subscribe&token=tok"
    payload = SNSNotification.model_validate(payload_dict)
    canonical = _canonical_string(payload)
    signature = key.sign(canonical, padding.PKCS1v15(), hashes.SHA1())
    payload.Signature = base64.b64encode(signature).decode()
    return payload


def test_verify_sns_signature_accepts_valid_signed_message(rsa_keypair_and_cert):
    key, cert_pem = rsa_keypair_and_cert
    cert_url = "https://sns.eu-west-2.amazonaws.com/cert.pem"
    payload = _signed_payload(key, cert_url)

    with patch("flip_api.utils.sns.requests.get") as get_mock:
        get_mock.return_value = MagicMock(content=cert_pem, raise_for_status=MagicMock())
        verify_sns_signature(payload)


def test_verify_sns_signature_rejects_tampered_message(rsa_keypair_and_cert):
    key, cert_pem = rsa_keypair_and_cert
    cert_url = "https://sns.eu-west-2.amazonaws.com/cert.pem"
    payload = _signed_payload(key, cert_url)
    payload.Message = "tampered"

    with patch("flip_api.utils.sns.requests.get") as get_mock:
        get_mock.return_value = MagicMock(content=cert_pem, raise_for_status=MagicMock())
        with pytest.raises(SNSVerificationError):
            verify_sns_signature(payload)


def test_verify_sns_signature_rejects_untrusted_cert_host(rsa_keypair_and_cert):
    key, _ = rsa_keypair_and_cert
    payload = _signed_payload(key, "https://attacker.example.com/cert.pem")

    with pytest.raises(SNSVerificationError):
        verify_sns_signature(payload)


def test_verify_sns_signature_rejects_non_https_cert_url(rsa_keypair_and_cert):
    key, _ = rsa_keypair_and_cert
    payload = _signed_payload(key, "http://sns.eu-west-2.amazonaws.com/cert.pem")

    with pytest.raises(SNSVerificationError):
        verify_sns_signature(payload)


def test_confirm_subscription_rejects_untrusted_subscribe_url():
    payload = SNSNotification.model_validate(
        {
            "Type": "SubscriptionConfirmation",
            "MessageId": "id",
            "TopicArn": "arn",
            "Message": "msg",
            "Timestamp": "2026-01-01T00:00:00.000Z",
            "SignatureVersion": "1",
            "Signature": "AAA",
            "SigningCertURL": "https://sns.eu-west-2.amazonaws.com/cert.pem",
            "Token": "tok",
            "SubscribeURL": "https://attacker.example.com/confirm",
        }
    )
    with pytest.raises(SNSVerificationError):
        confirm_subscription(payload)


def test_confirm_subscription_fetches_amazon_url_only():
    payload = SNSNotification.model_validate(
        {
            "Type": "SubscriptionConfirmation",
            "MessageId": "id",
            "TopicArn": "arn",
            "Message": "msg",
            "Timestamp": "2026-01-01T00:00:00.000Z",
            "SignatureVersion": "1",
            "Signature": "AAA",
            "SigningCertURL": "https://sns.eu-west-2.amazonaws.com/cert.pem",
            "Token": "tok",
            "SubscribeURL": "https://sns.eu-west-2.amazonaws.com/?Subscribe&token=tok",
        }
    )
    with patch("flip_api.utils.sns.requests.get") as get_mock:
        get_mock.return_value = MagicMock(raise_for_status=MagicMock())
        confirm_subscription(payload)
        get_mock.assert_called_once_with(payload.SubscribeURL, timeout=5)
