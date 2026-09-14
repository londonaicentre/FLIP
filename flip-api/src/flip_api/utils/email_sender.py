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
from collections.abc import Mapping
from typing import Any

import boto3

from flip_api.config import get_settings
from flip_api.utils.logger import logger

# Substrings marking a template-data key whose value must never reach the
# logs. The console backend logs the payload, and flip-xnat-credentials
# carries the user's decrypted XNAT password. Matched as substrings rather
# than exact names, and at every depth of the payload: a denylist guarding a
# live credential has to fail safe, so a future `temp_password`, `api_secret`
# or `access_token` field is redacted without anyone remembering to update
# this set, and without depending on the payload staying flat the way today's
# ISesTemplateData shapes happen to be.
_REDACTED_KEY_MARKERS = ("password", "secret", "token", "credential")

# Cap on a single logged template value. reason_for_access arrives on the
# unauthenticated POST /users/access with no length bound.
_MAX_LOGGED_VALUE_CHARS = 200


class EmailDispatchError(Exception):
    """Raised when dispatch failed systemically rather than for one recipient.

    Callers that key a retry off a clean return (see
    ``private_services/imaging_notifications``) need to tell "this one address
    was rejected" apart from "nothing can be sent at all", so the latter is
    surfaced rather than logged and swallowed. The distinction is drawn on the
    exception type — a ``BotoCoreError`` (no region, no credentials, dead
    endpoint) is systemic, a ``ClientError`` is that address's own problem —
    not on how many recipients happened to fail, because the common batch here
    is a single recipient and the two are indistinguishable by count.
    """


def send_templated_email(recipient: str, template_name: str, template_data: dict[str, Any]) -> None:
    """Send one templated email to one recipient via the configured backend.

    Dispatches on the ``EMAIL_BACKEND`` setting: ``"ses"`` sends through AWS
    SESv2 using the account-level template of that name (prod/stag);
    ``"console"`` logs the would-be email instead (the development default),
    masking the value of any secret-shaped key (see ``_redact``).

    The console backend logs and returns — it does no I/O and no
    serialisation — so in development a caller's success-path bookkeeping
    (e.g. ``AccessRequest.email_notified``) runs as if the email were
    delivered. The SES backend lets boto3/SES errors propagate unchanged —
    callers keep their existing best-effort handling.

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
    """Log the would-be email (redacting sensitive fields) instead of sending it.

    Logged at WARNING, not INFO: this records that a requested action was
    deliberately not performed, and it is the only trace that the email was
    suppressed. At INFO a developer running LOG_LEVEL=WARNING would get no
    record at all while callers still flag the notification as sent.

    The payload is logged to make the dev backend useful, so it reaches the
    logs in clear apart from the redacted keys — for the access-request
    template that includes the requester's name and email. Values are
    truncated to keep an unbounded free-text field (reason_for_access, from
    an unauthenticated endpoint) from dominating the log.
    """
    redacted = {key: _redact(key, value) for key, value in template_data.items()}
    logger.warning(
        f"Email suppressed (EMAIL_BACKEND=console): template '{template_name}' to {recipient}, data={redacted}"
    )


def _redact(key: str, value: Any) -> Any:
    """Mask a sensitive value, keeping its length as a diagnostic, and truncate the rest.

    Recurses into nested mappings, re-matching the markers against each
    nested key, so a secret one level down is redacted rather than
    stringified wholesale by a parent key that matches nothing
    (``{"user": {"password": ...}}``). Sequence elements are recursed under
    the key they were found on, so a list of tokens is masked element-wise.

    Args:
        key (str): Template-data key the value was found under.
        value (Any): Value to render for the log line.

    Returns:
        Any: The rendered value — a string for a leaf, or the same container
        shape with every leaf rendered.
    """
    if any(marker in key.lower() for marker in _REDACTED_KEY_MARKERS):
        # Keep the length: an empty or whitespace-only secret means a broken
        # decrypt(), which would otherwise be indistinguishable from a good one.
        return f"***REDACTED*** ({len(str(value))} chars)"
    if isinstance(value, Mapping):
        return {nested: _redact(str(nested), nested_value) for nested, nested_value in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(key, item) for item in value]
    rendered = str(value)
    return rendered if len(rendered) <= _MAX_LOGGED_VALUE_CHARS else f"{rendered[:_MAX_LOGGED_VALUE_CHARS]}…"
