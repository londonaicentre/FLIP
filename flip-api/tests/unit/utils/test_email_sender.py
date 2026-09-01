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

"""Unit tests for the templated-email backends (FLIP#919)."""

import json
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from flip_api.utils.email_sender import send_templated_email

SECRET_PASSWORD = "sup3r-s3cret-xnat-pw"
CREDENTIALS_DATA = {
    "trust_name": "Trust_1",
    "project_name": "Test Project",
    "username": "user1",
    "password": SECRET_PASSWORD,
}


def _settings(backend: str) -> SimpleNamespace:
    return SimpleNamespace(
        EMAIL_BACKEND=backend,
        AWS_REGION="mock-region",
        AWS_SES_SENDER_EMAIL_ADDRESS="sender@example.com",
    )


@pytest.fixture
def console_backend():
    with patch("flip_api.utils.email_sender.get_settings", return_value=_settings("console")):
        yield


@pytest.fixture
def ses_backend():
    with patch("flip_api.utils.email_sender.get_settings", return_value=_settings("ses")):
        yield


def test_console_backend_does_not_call_aws(console_backend, caplog):
    """The dev default must not construct an AWS client — that is the whole point (#919)."""
    with patch("flip_api.utils.email_sender.boto3") as mock_boto3:
        with caplog.at_level(logging.INFO):
            send_templated_email(
                recipient="user1@test.com",
                template_name="flip-xnat-credentials",
                template_data=CREDENTIALS_DATA,
            )

    mock_boto3.client.assert_not_called()
    assert "flip-xnat-credentials" in caplog.text
    assert "user1@test.com" in caplog.text


def test_console_backend_redacts_the_password(console_backend, caplog):
    """The XNAT credentials template carries a decrypted password; it must never reach the logs."""
    with caplog.at_level(logging.INFO):
        send_templated_email(
            recipient="user1@test.com",
            template_name="flip-xnat-credentials",
            template_data=CREDENTIALS_DATA,
        )

    assert SECRET_PASSWORD not in caplog.text
    assert "***REDACTED***" in caplog.text
    # Non-secret fields stay legible so the log is still useful in dev.
    assert "Trust_1" in caplog.text
    assert "user1" in caplog.text


def test_ses_backend_sends_templated_email(ses_backend):
    """The SES path builds a sesv2 client and sends the template with a JSON payload."""
    with patch("flip_api.utils.email_sender.boto3.client") as mock_client_factory:
        mock_client = MagicMock()
        mock_client_factory.return_value = mock_client

        send_templated_email(
            recipient="admin@example.com",
            template_name="flip-access-request",
            template_data={"email": "a@b.com", "name": "A B", "purpose": "research"},
        )

    mock_client_factory.assert_called_once_with("sesv2", region_name="mock-region")
    mock_client.send_email.assert_called_once()
    kwargs = mock_client.send_email.call_args.kwargs
    assert kwargs["FromEmailAddress"] == "sender@example.com"
    assert kwargs["Destination"] == {"ToAddresses": ["admin@example.com"]}
    assert kwargs["Content"]["Template"]["TemplateName"] == "flip-access-request"
    assert json.loads(kwargs["Content"]["Template"]["TemplateData"]) == {
        "email": "a@b.com",
        "name": "A B",
        "purpose": "research",
    }


def test_ses_backend_propagates_client_error(ses_backend):
    """Send failures propagate: callers own the best-effort handling, not this module."""
    with patch("flip_api.utils.email_sender.boto3.client") as mock_client_factory:
        mock_client = MagicMock()
        mock_client.send_email.side_effect = ClientError(
            {"Error": {"Code": "MessageRejected", "Message": "Email address is not verified"}}, "SendEmail"
        )
        mock_client_factory.return_value = mock_client

        with pytest.raises(ClientError):
            send_templated_email(
                recipient="admin@example.com",
                template_name="flip-access-request",
                template_data={"email": "a@b.com"},
            )
