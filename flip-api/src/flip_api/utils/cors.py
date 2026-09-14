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

"""CORS allowlist derivation for the FLIP API.

Sourced from the Cognito app client's ``CallbackURLs`` so there is one Terraform-owned
list of trusted UI origins per environment rather than two config surfaces that can
drift. The UI authenticates with ``USER_SRP_AUTH`` rather than an OAuth2 redirect, so
those URLs are never used as a login callback — flip-api reads them purely as the
declared UI-origin list.

This is the only Cognito call the ASGI app itself makes at startup (``main.py``'s
lifespan). The container contacts Cognito earlier too, from the separate seeding
process ``entrypoint.sh`` runs before uvicorn — see ``db/seed/main_users.py``.
"""

from urllib.parse import urlparse

from flip_api.config import get_settings
from flip_api.utils.cognito_helpers import _cognito_client

_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin_from_url(url: str) -> str | None:
    """Return ``scheme://host[:port]`` for ``url``, omitting ports that match the scheme default.

    Browsers strip default ports from the ``Origin`` header (RFC 6454), so an allowlist entry
    like ``https://localhost:443`` would never match an actual request — normalize before use.
    Returns ``None`` for URLs without a usable scheme/host.
    """
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.hostname:
        return None
    host = parsed.hostname
    port = parsed.port
    if port is None or port == _DEFAULT_PORTS.get(parsed.scheme):
        return f"{parsed.scheme}://{host}"
    return f"{parsed.scheme}://{host}:{port}"


def get_cors_allowed_origins() -> list[str]:
    """Derive the CORS allowlist from the Cognito user pool client's CallbackURLs.

    The same Cognito app client that authenticates UI logins already enumerates the trusted UI
    origins per environment (see ``deploy/providers/AWS/services.tf``). Reusing it as the CORS
    allowlist keeps "where users can sign in" and "where the UI may call this API" in lockstep,
    without a separate env var.

    Returns:
        list[str]: Unique normalized origins (``scheme://host[:port]``) suitable for
        ``CORSMiddleware(allow_origins=...)``.
    """
    settings = get_settings()
    response = _cognito_client().describe_user_pool_client(
        UserPoolId=settings.AWS_COGNITO_USER_POOL_ID,
        ClientId=settings.AWS_COGNITO_APP_CLIENT_ID,
    )
    callback_urls: list[str] = response.get("UserPoolClient", {}).get("CallbackURLs", []) or []

    seen: set[str] = set()
    origins: list[str] = []
    for url in callback_urls:
        origin = _origin_from_url(url)
        if origin and origin not in seen:
            seen.add(origin)
            origins.append(origin)
    return origins
