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

"""Origin normalisation for the CORS allowlist.

The allowlist itself comes from the identity provider
(``IdentityProvider.allowed_origins()``): the Cognito app client's
``CallbackURLs`` or the Keycloak client's redirect URIs — one provider-owned
list of trusted UI origins per environment rather than two config surfaces
that can drift. This module only turns those URLs into origins the browser's
``Origin`` header will actually match.
"""

from collections.abc import Iterable
from urllib.parse import urlparse

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


def normalise_origins(urls: Iterable[str]) -> list[str]:
    """Unique normalised origins for ``urls``, in first-seen order, unusable entries dropped.

    Returns:
        list[str]: Values suitable for ``CORSMiddleware(allow_origins=...)``.
    """
    seen: set[str] = set()
    origins: list[str] = []
    for url in urls:
        origin = _origin_from_url(url)
        if origin and origin not in seen:
            seen.add(origin)
            origins.append(origin)
    return origins
