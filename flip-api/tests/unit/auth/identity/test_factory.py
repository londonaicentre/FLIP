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

"""Provider selection by AUTH_BACKEND and the FastAPI dependency around it (FLIP#919)."""

from unittest.mock import MagicMock, patch

import pytest

from flip_api.auth.identity import factory
from flip_api.auth.identity.cognito import CognitoIdentityProvider
from flip_api.auth.identity.factory import build_identity_provider, get_identity_provider
from flip_api.auth.identity.http import HttpIdentityProvider


def _settings(backend: str) -> MagicMock:
    settings = MagicMock()
    settings.AUTH_BACKEND = backend
    settings.AWS_REGION = "eu-west-2"
    settings.AWS_COGNITO_USER_POOL_ID = "pool-id"
    settings.AWS_COGNITO_APP_CLIENT_ID = "client-id"
    return settings


@pytest.fixture(autouse=True)
def _fresh_cache():
    factory._cached_provider.cache_clear()
    yield
    factory._cached_provider.cache_clear()


def test_cognito_backend_builds_the_cognito_provider():
    provider = build_identity_provider(_settings("cognito"))
    assert isinstance(provider, CognitoIdentityProvider)
    assert provider.backend == "cognito"


def test_unknown_backend_is_a_configuration_error():
    with pytest.raises(ValueError, match="AUTH_BACKEND"):
        build_identity_provider(_settings("ldap"))


def test_dependency_wraps_one_process_wide_provider_in_the_http_translator():
    with patch("flip_api.auth.identity.factory.get_settings", return_value=_settings("cognito")):
        first = get_identity_provider()
        second = get_identity_provider()
    assert isinstance(first, HttpIdentityProvider)
    assert isinstance(second, HttpIdentityProvider)
    # The raw provider (and its MFA cache and client) is shared; only the thin wrapper is per call.
    assert first.inner is second.inner
    assert isinstance(first.inner, CognitoIdentityProvider)


def test_build_never_touches_the_cache():
    """Scripts and the seed build their own provider; the FastAPI dependency owns the singleton."""
    with patch("flip_api.auth.identity.factory.get_settings", return_value=_settings("cognito")):
        cached = get_identity_provider().inner
    assert build_identity_provider(_settings("cognito")) is not cached
