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

"""Rate limiter configuration for trust-facing API endpoints."""

import hashlib

from fastapi import Request
from slowapi import Limiter

from flip_api.config import get_settings


def _trust_name_key(request: Request) -> str:
    """Per-trust rate-limit key for trust-facing endpoints.

    The trust-poll endpoints (``/tasks/pending``, ``/trust/heartbeat``) carry no
    ``trust_name`` path param and sit behind a TLS-terminating proxy (CloudFront
    → ALB → flip-api), so ``request.client.host`` is the proxy address — the same
    for every trust. Keying on it collapses each endpoint's per-trust limit into
    a single global one shared by all trusts. Key on a hash of the trust API key
    header instead, so every trust is rate-limited independently. Fall back to
    the ``trust_name`` path param, then the client host.

    Args:
        request (Request): The incoming FastAPI request.

    Returns:
        str: A stable per-trust key — ``trust:<hash>`` when the API key header is
        present, otherwise the ``trust_name`` path param or the client host.
    """
    api_key = request.headers.get(get_settings().TRUST_API_KEY_HEADER)
    if api_key:
        # SHA-256 is appropriate here: the input is a 256-bit random token from
        # `secrets.token_urlsafe(32)`, not a user-chosen password. Slow KDFs
        # (bcrypt/argon2) only defend against low-entropy inputs; against 256-bit
        # random keys they provide no security benefit. The hash is only used as
        # a rate-limit bucket key — it's not stored or transmitted. CodeQL flags
        # this as `py/weak-sensitive-data-hashing` but the rule targets password
        # hashing — false positive. Same pattern as access_manager.py.
        return "trust:" + hashlib.sha256(api_key.encode()).hexdigest()[:16]
    return request.path_params.get("trust_name", request.client.host if request.client else "unknown")


limiter = Limiter(key_func=_trust_name_key)
