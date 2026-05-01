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

"""Trust-internal service authentication for the data-access-api.

The data-access-api executes arbitrary SQL against the OMOP database via a
service account. Without caller authentication, any container on the trust
Docker network or any operator with SSM port-forward access can run the same
queries as the service account. This module enforces a shared-secret check on
the ``/cohort`` router: callers (trust-api, imaging-api, fl-client) send the
plaintext ``TRUST_INTERNAL_SERVICE_KEY`` in a header, and data-access-api
compares it to its own copy of the same key using constant-time comparison.

The key is held in plaintext by every trust-internal service (sender or
receiver). See ``imaging_api/utils/internal_auth.py`` for the rationale —
the same module-level docstring applies here.
"""

import hmac

from fastapi import HTTPException, Security, status
from fastapi.security.api_key import APIKeyHeader

from data_access_api.config import get_settings
from data_access_api.utils.logger import logger

_settings = get_settings()

internal_key_header_scheme = APIKeyHeader(
    name=_settings.TRUST_INTERNAL_SERVICE_KEY_HEADER,
    auto_error=False,
)


def authenticate_internal_service(api_key: str | None = Security(internal_key_header_scheme)) -> None:
    """Authenticate a trust-internal caller (trust-api, imaging-api, fl-client).

    Args:
        api_key (str | None): The plaintext key from the request header.

    Raises:
        HTTPException: 401 if the key is missing, unconfigured, or invalid.
    """
    if not api_key:
        logger.warning("Trust-internal service authentication failed: key missing from request.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated: trust-internal service key is missing.",
        )

    expected = get_settings().TRUST_INTERNAL_SERVICE_KEY
    if not expected:
        # Fail closed: refusing to start without the key would block /health too.
        # Returning 401 keeps health checks working while blocking every privileged route.
        logger.warning("Trust-internal service authentication failed: TRUST_INTERNAL_SERVICE_KEY not configured.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Trust-internal service auth not configured.",
        )

    if not hmac.compare_digest(api_key.encode(), expected.encode()):
        logger.warning("Trust-internal service authentication failed: invalid key.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid trust-internal service key.",
        )
