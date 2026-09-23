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

"""Provider selection by ``AUTH_BACKEND`` (FLIP#919)."""

from functools import lru_cache

from flip_api.auth.identity.base import IdentityProvider
from flip_api.auth.identity.cognito import CognitoIdentityProvider
from flip_api.auth.identity.http import HttpIdentityProvider
from flip_api.config import DevSettings, ProdSettings, get_settings


def build_identity_provider(settings: DevSettings | ProdSettings | None = None) -> IdentityProvider:
    """Construct the provider ``settings.AUTH_BACKEND`` names.

    Raw (no HTTP translation) and never cached: the boot seed and operator
    scripts build their own, and the FastAPI dependency below owns the
    process-wide instance.
    """
    settings = settings or get_settings()
    if settings.AUTH_BACKEND == "cognito":
        return CognitoIdentityProvider(settings)
    raise ValueError(f"Unsupported AUTH_BACKEND: {settings.AUTH_BACKEND!r}")


@lru_cache(maxsize=1)
def _cached_provider() -> IdentityProvider:
    return build_identity_provider(get_settings())


def get_identity_provider() -> IdentityProvider:
    """FastAPI dependency: the shared provider, with its errors translated to HTTP.

    Tests override it with ``app.dependency_overrides[get_identity_provider]``.
    """
    return HttpIdentityProvider(_cached_provider())
