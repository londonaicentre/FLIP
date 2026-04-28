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

"""AWS SNS HTTPS-subscription helpers: signature verification + subscription confirmation.

The malware-scan SNS topic posts to a public flip-api endpoint that is not
behind Cognito (SNS does not carry user credentials). To stop attackers from
forging scan results, every payload's signature is checked against the public
key in its ``SigningCertURL`` — and that URL must point at an Amazon-owned
host. The verification logic follows the canonical-string format documented at
https://docs.aws.amazon.com/sns/latest/dg/sns-verify-signature-of-message-http-endpoint.html.
"""

import base64
import re
from urllib.parse import urlparse

import requests
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.x509 import load_pem_x509_certificate

from flip_api.domain.schemas.file import SNSNotification
from flip_api.utils.logger import logger

# Only certificates served from one of these hosts are accepted as SNS signing
# certificates. AWS publishes its SNS signing certs under regional sns.*
# subdomains of amazonaws.com / amazonaws.com.cn. Anything else is rejected.
_TRUSTED_CERT_HOST_RE = re.compile(r"^sns\.[a-z0-9\-]+\.amazonaws\.com(\.cn)?$")

# The two canonical-string field orderings AWS documents per Type. Order
# matters — the bytes must be reproduced exactly as AWS signs them.
_NOTIFICATION_FIELDS = ("Message", "MessageId", "Subject", "Timestamp", "TopicArn", "Type")
_SUBSCRIPTION_FIELDS = ("Message", "MessageId", "SubscribeURL", "Timestamp", "Token", "TopicArn", "Type")


class SNSVerificationError(Exception):
    """Raised when an SNS message fails signature verification."""


def _canonical_string(payload: SNSNotification) -> bytes:
    """Build the canonical string AWS signs for ``payload``.

    Args:
        payload: The parsed SNS HTTPS envelope.

    Returns:
        The canonical UTF-8 bytes that should match ``payload.Signature``
        when verified against the SigningCert public key.
    """
    fields: tuple[str, ...]
    if payload.Type in ("SubscriptionConfirmation", "UnsubscribeConfirmation"):
        fields = _SUBSCRIPTION_FIELDS
    else:
        fields = _NOTIFICATION_FIELDS

    parts: list[str] = []
    for field in fields:
        value = getattr(payload, field, None)
        if value is None:
            # Optional fields (e.g. Subject) are skipped entirely if not present;
            # AWS' canonical-string algorithm omits the line rather than emitting
            # an empty value.
            continue
        parts.append(field)
        parts.append(str(value))
    return ("\n".join(parts) + "\n").encode("utf-8")


def _fetch_signing_cert(url: str) -> bytes:
    """Download the SNS signing certificate, refusing untrusted hosts.

    Args:
        url: The ``SigningCertURL`` from the SNS payload.

    Returns:
        The PEM-encoded certificate bytes.

    Raises:
        SNSVerificationError: If the URL is not HTTPS or the host is not an
            ``sns.*.amazonaws.com`` (cert spoofing defence) host, or the fetch
            fails.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise SNSVerificationError(f"SigningCertURL must be HTTPS, got {parsed.scheme}.")
    if not _TRUSTED_CERT_HOST_RE.match(parsed.hostname or ""):
        raise SNSVerificationError(f"Untrusted SigningCertURL host: {parsed.hostname}.")
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise SNSVerificationError(f"Failed to fetch SigningCertURL: {exc}") from exc
    return response.content


def verify_sns_signature(payload: SNSNotification) -> None:
    """Verify the signature on an SNS message.

    Args:
        payload: The parsed SNS HTTPS envelope.

    Raises:
        SNSVerificationError: If the signature is missing, the cert host is not
            trusted, or the signature does not match the public key.
    """
    if payload.SignatureVersion not in ("1", "2"):
        raise SNSVerificationError(f"Unsupported SignatureVersion: {payload.SignatureVersion}.")

    cert_bytes = _fetch_signing_cert(payload.SigningCertURL)
    try:
        cert = load_pem_x509_certificate(cert_bytes)
    except ValueError as exc:
        raise SNSVerificationError(f"Failed to parse signing certificate: {exc}") from exc

    public_key = cert.public_key()
    # SNS only ever publishes RSA signing certs; reject anything else rather
    # than fall through to a verify call that does not match the signature
    # algorithm AWS uses.
    if not isinstance(public_key, RSAPublicKey):
        raise SNSVerificationError("Signing certificate is not RSA.")

    try:
        signature = base64.b64decode(payload.Signature)
    except (ValueError, TypeError) as exc:
        raise SNSVerificationError(f"Invalid Signature encoding: {exc}") from exc

    canonical = _canonical_string(payload)
    hash_algo: hashes.HashAlgorithm = hashes.SHA1() if payload.SignatureVersion == "1" else hashes.SHA256()

    try:
        public_key.verify(signature, canonical, padding.PKCS1v15(), hash_algo)
    except InvalidSignature as exc:
        raise SNSVerificationError("SNS signature did not match canonical message bytes.") from exc


def confirm_subscription(payload: SNSNotification) -> None:
    """Confirm an SNS HTTPS subscription by fetching its ``SubscribeURL``.

    AWS sends a one-shot ``SubscriptionConfirmation`` message when a topic is
    subscribed to an HTTPS endpoint; the subscription is only activated once
    the endpoint GETs the ``SubscribeURL`` carried in that message.

    Args:
        payload: The parsed SNS HTTPS envelope (Type = SubscriptionConfirmation).

    Raises:
        SNSVerificationError: If ``SubscribeURL`` is missing, the host is not a
            trusted Amazon SNS host, or the GET fails.
    """
    if not payload.SubscribeURL:
        raise SNSVerificationError("SubscriptionConfirmation message missing SubscribeURL.")

    parsed = urlparse(payload.SubscribeURL)
    if parsed.scheme != "https" or not _TRUSTED_CERT_HOST_RE.match(parsed.hostname or ""):
        raise SNSVerificationError(f"Refusing to confirm subscription via untrusted host {parsed.hostname}.")

    try:
        response = requests.get(payload.SubscribeURL, timeout=5)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise SNSVerificationError(f"Failed to confirm SNS subscription: {exc}") from exc
    logger.info(f"Confirmed SNS subscription for topic {payload.TopicArn}.")


def load_signing_cert(cert_bytes: bytes) -> bytes:
    """Re-export of PEM cert loading (used for testability).

    Args:
        cert_bytes: PEM-encoded x509 certificate.

    Returns:
        The PEM bytes serialised back via ``cryptography`` (round-trip useful
        for tests that want to assert the cert parses).
    """
    cert = load_pem_x509_certificate(cert_bytes)
    return cert.public_bytes(serialization.Encoding.PEM)
