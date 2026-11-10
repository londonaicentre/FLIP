# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
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

"""Service-key authentication for the FL API.

The FL API drives a whole FL net (submit, abort, shut down, map nodes to trusts), and its only
legitimate callers are the hub's own services: flip-api and, on Flower, the SuperNode key
registration one-shot. Every router except health depends on ``require_fl_api_key``: the caller
sends the plaintext ``FL_API_KEY`` in the ``FL_API_KEY_HEADER`` header (default ``X-FL-API-Key``)
and the API compares it to its own copy in constant time.

The key is dedicated to this hop. It is not ``INTERNAL_SERVICE_KEY`` (fl-server → flip-api), so
the FL API never holds a credential that opens the hub, and a trust-side key opens nothing here.

Fail closed: with no key configured every protected route answers 401 while ``/health`` keeps
serving liveness, and the boot log says so once.

Byte-identical in the FLARE and Flower fl-api copies — ``scripts/check_fl_api_validation_sync.sh``.
"""

import hmac
import os

from fastapi import HTTPException, Security, status
from fastapi.security.api_key import APIKeyHeader

from fl_api.utils.logger import logger

FL_API_KEY_HEADER = os.getenv("FL_API_KEY_HEADER", "X-FL-API-Key")

fl_api_key_header_scheme = APIKeyHeader(name=FL_API_KEY_HEADER, auto_error=False)


def configured_fl_api_key() -> str:
    """The key this FL API expects, read per call so a test can unset it.

    Returns:
        str: ``FL_API_KEY``, or an empty string when unset.
    """
    return os.getenv("FL_API_KEY", "")


def warn_if_fl_api_key_missing() -> None:
    """Report an unset ``FL_API_KEY`` where an operator looks first: the boot log."""
    if not configured_fl_api_key():
        logger.warning("FL_API_KEY is not set: every route except /health answers 401 until it is.")


def require_fl_api_key(api_key: str | None = Security(fl_api_key_header_scheme)) -> None:
    """Authenticate a hub-internal caller (flip-api, the Flower key registration one-shot).

    Args:
        api_key (str | None): The plaintext key from the request header.

    Raises:
        HTTPException: 401 if the key is missing, unconfigured, or invalid.
    """
    if not api_key:
        logger.warning("FL API authentication failed: key missing from request.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated: FL API key is missing.",
        )

    expected = configured_fl_api_key()
    if not expected:
        # Refusing to start would take /health down with it; 401 keeps liveness and blocks everything else.
        logger.warning("FL API authentication failed: FL_API_KEY not configured.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="FL API auth not configured.",
        )

    if not hmac.compare_digest(api_key.encode(), expected.encode()):
        logger.warning("FL API authentication failed: invalid key.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid FL API key.",
        )
