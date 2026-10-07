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

"""The identity-provider seam: what FLIP needs from a user directory (FLIP#919).

Token *verification* is generic OIDC (:mod:`flip_api.auth.token_verifier`);
this interface covers the half that has no standard — listing, inviting,
disabling and deleting users, and reading or clearing their MFA state. One
implementation per backend (:mod:`.cognito`, :mod:`.keycloak`); the
template methods here carry the behaviour every backend shares.
"""

import threading
import time
from abc import ABC, abstractmethod
from typing import ClassVar
from uuid import UUID

from flip_api.domain.schemas.users import CognitoUser
from flip_api.utils.logger import logger

# Positive MFA state is cached briefly because ``verify_token`` asks on every
# authenticated request. Negative state is never cached so a user finishing
# enrolment is admitted on their very next call, and an admin reset drops the
# entry so it takes effect immediately rather than after the TTL.
_MFA_STATE_TTL_SECONDS = 60.0


class IdentityProvider(ABC):
    """A user directory FLIP can authenticate against.

    Every method raises the neutral exceptions in :mod:`.errors`; the
    FastAPI layer translates them (:class:`.http.HttpIdentityProvider`).
    Identifiers: ``user_id`` is the provider's stable subject (the token
    ``sub``, a UUID in every supported backend) and ``username`` its
    principal name — the email address in both FLIP realms.
    """

    backend: ClassVar[str]

    # Created on first use rather than in __init__, so a subclass that never
    # calls super().__init__() still gets a working, per-instance cache.
    _mfa_state_cache: dict[str, tuple[bool, float]]
    _mfa_state_cache_lock: threading.Lock

    def _mfa_cache(self) -> tuple[dict[str, tuple[bool, float]], threading.Lock]:
        try:
            return self._mfa_state_cache, self._mfa_state_cache_lock
        except AttributeError:
            self._mfa_state_cache = {}
            self._mfa_state_cache_lock = threading.Lock()
            return self._mfa_state_cache, self._mfa_state_cache_lock

    # --- directory -------------------------------------------------------------------------

    @abstractmethod
    def list_users(self) -> list[CognitoUser]:
        """Return every user in the directory."""

    @abstractmethod
    def get_user(self, *, user_id: UUID | str | None = None, email: str | None = None) -> CognitoUser:
        """Return the user with ``user_id`` or ``email``.

        Raises:
            InvalidIdentifierError: If neither identifier is given, or the given one is malformed.
            UserNotFoundError: If no user matches.
        """

    def get_username(self, user_id: UUID | str) -> str:
        """Return the principal name (email) of the user with ``user_id``."""
        return self.get_user(user_id=user_id).email

    @abstractmethod
    def set_enabled(self, username: str, enabled: bool) -> None:
        """Enable or disable sign-in for ``username``."""

    @abstractmethod
    def create_user(self, email: str, *, suppress_invite: bool = False) -> UUID:
        """Create a user for ``email`` and return its id.

        The provider sends its own invitation (temporary password or
        set-password link) unless ``suppress_invite`` is set — operator
        tooling that sets a permanent password itself passes it.

        Raises:
            UserAlreadyExistsError: If ``email`` is already registered.
        """

    @abstractmethod
    def delete_user(self, username: str) -> None:
        """Delete ``username`` from the directory."""

    # --- MFA -------------------------------------------------------------------------------

    @abstractmethod
    def _fetch_mfa_enabled(self, username: str) -> bool:
        """Ask the backend whether ``username`` has an active TOTP credential."""

    @abstractmethod
    def _reset_mfa(self, username: str) -> None:
        """Clear ``username``'s TOTP credential and revoke their sessions."""

    def is_mfa_enabled(self, username: str) -> bool:
        """Whether ``username`` has active TOTP MFA, with positive results cached briefly."""
        cache, lock = self._mfa_cache()
        now = time.monotonic()
        with lock:
            cached = cache.get(username)
            if cached and cached[1] > now:
                return cached[0]

        enabled = self._fetch_mfa_enabled(username)
        if enabled:
            with lock:
                cache[username] = (True, time.monotonic() + _MFA_STATE_TTL_SECONDS)
        return enabled

    def reset_mfa(self, username: str) -> None:
        """Clear ``username``'s MFA and make the change visible on their next request."""
        self._reset_mfa(username)
        cache, lock = self._mfa_cache()
        with lock:
            cache.pop(username, None)

    # --- helpers shared by every backend --------------------------------------------------

    def filter_enabled_users(self, users: list[UUID]) -> list[UUID]:
        """Return the subset of ``users`` that exist and are enabled, warning about the rest."""
        if not users:
            return []

        user_map = {user.id: user for user in self.list_users()}
        valid_users: list[UUID] = []
        for user_id in users:
            if user_id in user_map and not user_map[user_id].is_disabled:
                valid_users.append(user_id)
            else:
                logger.warning(f"User {user_id} is either disabled or does not exist")
        return valid_users

    @abstractmethod
    def allowed_origins(self) -> list[str]:
        """The browser origins the provider trusts to sign users in — FLIP's CORS allowlist."""

    # --- operator tooling ------------------------------------------------------------------

    @abstractmethod
    def set_password(self, email: str, password: str) -> None:
        """Set a permanent password for ``email``. Never called by a router."""

    @abstractmethod
    def describe_target(self) -> str:
        """A one-line description of the directory, for interactive confirmations."""
