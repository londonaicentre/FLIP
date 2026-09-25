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

"""The one place identity-provider errors become HTTP status codes (FLIP#919).

Routers depend on the codes: ``set_user_roles`` tells 404 from 503 so the
registration step function can decide between rolling back and retrying,
``delete_user`` treats 404 as already-gone, the boot seed skips a 404. Keeping
the translation in a wrapper rather than in each provider means every backend
is held to the same table, and a provider can be used outside FastAPI (the
seed, the demo-user script) without HTTP in the loop.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from fastapi import HTTPException, status

from flip_api.auth.identity.base import IdentityProvider
from flip_api.auth.identity.errors import (
    IdentityProviderError,
    IdentityProviderUnavailable,
    InvalidIdentifierError,
    UserAlreadyExistsError,
    UserNotFoundError,
)
from flip_api.domain.schemas.users import CognitoUser

_STATUS_BY_ERROR: tuple[tuple[type[IdentityProviderError], int], ...] = (
    # Most specific first: every entry is a subclass of IdentityProviderError.
    (UserNotFoundError, status.HTTP_404_NOT_FOUND),
    (UserAlreadyExistsError, status.HTTP_400_BAD_REQUEST),
    (InvalidIdentifierError, status.HTTP_400_BAD_REQUEST),
    (IdentityProviderUnavailable, status.HTTP_503_SERVICE_UNAVAILABLE),
    (IdentityProviderError, status.HTTP_500_INTERNAL_SERVER_ERROR),
)


@contextmanager
def _as_http() -> Iterator[None]:
    try:
        yield
    except IdentityProviderError as exc:
        for error_type, status_code in _STATUS_BY_ERROR:
            if isinstance(exc, error_type):
                raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        raise  # unreachable: IdentityProviderError is the last entry


class HttpIdentityProvider(IdentityProvider):
    """Delegates to ``inner`` and rewrites its errors as ``HTTPException``.

    An ``HTTPException`` raised by ``inner`` itself passes through untouched,
    which is what lets router unit tests install a test double that speaks
    HTTP directly.
    """

    def __init__(self, inner: IdentityProvider) -> None:
        # No super().__init__(): the MFA cache belongs to the inner provider.
        self.inner = inner

    @property
    def backend(self) -> str:  # type: ignore[override]
        return self.inner.backend

    def list_users(self) -> list[CognitoUser]:
        with _as_http():
            return self.inner.list_users()

    def get_user(self, *, user_id: UUID | str | None = None, email: str | None = None) -> CognitoUser:
        with _as_http():
            return self.inner.get_user(user_id=user_id, email=email)

    def get_username(self, user_id: UUID | str) -> str:
        with _as_http():
            return self.inner.get_username(user_id)

    def set_enabled(self, username: str, enabled: bool) -> None:
        with _as_http():
            self.inner.set_enabled(username, enabled)

    def create_user(self, email: str, *, suppress_invite: bool = False) -> UUID:
        with _as_http():
            return self.inner.create_user(email, suppress_invite=suppress_invite)

    def delete_user(self, username: str) -> None:
        with _as_http():
            self.inner.delete_user(username)

    def _fetch_mfa_enabled(self, username: str) -> bool:
        with _as_http():
            return self.inner._fetch_mfa_enabled(username)

    def _reset_mfa(self, username: str) -> None:
        with _as_http():
            self.inner._reset_mfa(username)

    def is_mfa_enabled(self, username: str) -> bool:
        with _as_http():
            return self.inner.is_mfa_enabled(username)

    def reset_mfa(self, username: str) -> None:
        with _as_http():
            self.inner.reset_mfa(username)

    def filter_enabled_users(self, users: list[UUID]) -> list[UUID]:
        with _as_http():
            return self.inner.filter_enabled_users(users)

    def allowed_origins(self) -> list[str]:
        with _as_http():
            return self.inner.allowed_origins()

    def set_password(self, email: str, password: str) -> None:
        with _as_http():
            self.inner.set_password(email, password)

    def describe_target(self) -> str:
        with _as_http():
            return self.inner.describe_target()
