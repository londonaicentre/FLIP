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

"""Templated email dispatch: SESv2 in production, console logging in development (FLIP#919)."""

import json
from typing import Any

import boto3

from flip_api.config import get_settings
from flip_api.utils.logger import logger

# Template-data keys whose values must never reach the logs. The console
# backend logs the payload, and flip-xnat-credentials carries the user's
# decrypted XNAT password.
_REDACTED_TEMPLATE_FIELDS = frozenset({"password"})


def send_templated_email(recipient: str, template_name: str, template_data: dict[str, Any]) -> None:
    """Send one templated email to one recipient via the configured backend.

    Dispatches on the ``EMAIL_BACKEND`` setting: ``"ses"`` sends through AWS
    SESv2 using the account-level template of that name (prod/stag);
    ``"console"`` logs the would-be email instead (the development default),
    with ``_REDACTED_TEMPLATE_FIELDS`` values masked.

    The console backend never raises, so in development a caller's
    success-path bookkeeping (e.g. ``AccessRequest.email_notified``) runs as
    if the email were delivered. The SES backend lets boto3/SES errors
    propagate unchanged — callers keep their existing best-effort handling.

    Args:
        recipient (str): Destination email address.
        template_name (str): SES template name (see ``utils/constants.py``).
        template_data (dict[str, Any]): Template substitution payload.

    Returns:
        None
    """
    if get_settings().EMAIL_BACKEND == "console":
        _send_via_console(recipient, template_name, template_data)
    else:
        _send_via_ses(recipient, template_name, template_data)


def _send_via_ses(recipient: str, template_name: str, template_data: dict[str, Any]) -> None:
    """Send through AWS SESv2; boto3/SES exceptions propagate to the caller."""
    settings = get_settings()
    sesv2 = boto3.client("sesv2", region_name=settings.AWS_REGION)
    sesv2.send_email(
        FromEmailAddress=settings.AWS_SES_SENDER_EMAIL_ADDRESS,
        Destination={"ToAddresses": [recipient]},
        Content={
            "Template": {
                "TemplateName": template_name,
                "TemplateData": json.dumps(template_data, default=str),
            }
        },
    )


def _send_via_console(recipient: str, template_name: str, template_data: dict[str, Any]) -> None:
    """Log the would-be email (redacting sensitive fields) instead of sending it."""
    redacted = {
        key: "***REDACTED***" if key in _REDACTED_TEMPLATE_FIELDS else value
        for key, value in template_data.items()
    }
    logger.info(f"Email suppressed (EMAIL_BACKEND=console): template '{template_name}' to {recipient}, data={redacted}")
