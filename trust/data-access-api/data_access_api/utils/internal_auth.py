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

The trust-internal key gates every ``/cohort`` route, but it does not
distinguish callers: fl-client holds it (legitimately — it reads the frozen
cohort via ``get_dataframe`` and pulls imaging), so it alone cannot separate
"may read the approved cohort" from "may DEFINE the approved cohort". The
snapshot create/delete routes — which materialise and destroy the artefact
every training round then trains on — therefore carry a second gate,
``authenticate_cohort_admin``: proof of possessing ``AES_KEY_BASE64``. trust-api
and data-access-api hold that key (they encrypt/decrypt hub payloads with it);
fl-client deliberately does not. The proof is the SHA-256 of the key, never the
key itself (FLIP#857).
"""

import hashlib
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

cohort_admin_header_scheme = APIKeyHeader(
    name=_settings.COHORT_ADMIN_KEY_HEADER,
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


# Returned when a caller is trust-internal-authenticated but is not a cohort admin. Fixed and
# generic — it must not reveal whether the proof was absent or merely wrong, nor confirm the
# project exists.
_NOT_COHORT_ADMIN_DETAIL = "Not authorised to define the cohort snapshot."


def cohort_admin_proof() -> str:
    """The credential the cohort-write routes require: SHA-256 of the trust's AES key.

    Possession of ``AES_KEY_BASE64`` is what separates the services trusted to define a
    project's approved cohort (trust-api, data-access-api) from those that only consume it
    (fl-client, which runs researcher code and holds no AES key). Sending the digest rather
    than the key keeps the encryption key off the wire and out of logs — a captured header is
    a replayable token scoped to two routes, never the key itself.

    Returns:
        str: The hex SHA-256 digest of ``AES_KEY_BASE64``.
    """
    return hashlib.sha256(get_settings().AES_KEY_BASE64.encode()).hexdigest()


def authenticate_cohort_admin(proof: str | None = Security(cohort_admin_header_scheme)) -> None:
    """Authorise a cohort-defining write (snapshot create/delete) on top of trust-internal auth.

    Layered under ``authenticate_internal_service`` on the write router, so it only runs for
    callers that already passed the trust-internal key check — hence 403 (authenticated, not
    authorised) rather than 401. fl-client passes the first gate and fails this one; trust-api
    passes both.

    Args:
        proof (str | None): The SHA-256-of-AES-key digest from the request header.

    Raises:
        HTTPException: 403 if the proof is missing, invalid, or the AES key is unconfigured.
    """
    aes_key = get_settings().AES_KEY_BASE64
    if not aes_key:
        # Fail closed. AES_KEY_BASE64 is a required setting, so this is defensive: an empty
        # value would make sha256("") a universally-guessable proof. Refuse rather than admit it.
        logger.warning("Cohort-admin authorization failed: AES_KEY_BASE64 not configured.")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_NOT_COHORT_ADMIN_DETAIL)

    if not proof:
        logger.warning("Cohort-admin authorization failed: proof missing from request.")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_NOT_COHORT_ADMIN_DETAIL)

    if not hmac.compare_digest(proof, cohort_admin_proof()):
        logger.warning("Cohort-admin authorization failed: invalid proof.")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_NOT_COHORT_ADMIN_DETAIL)
