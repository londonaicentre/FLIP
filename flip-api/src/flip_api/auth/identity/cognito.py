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

"""AWS Cognito as the identity provider (stag/prod, and dev with ``AUTH_BACKEND=cognito``)."""

import logging
from typing import Any
from uuid import UUID

import boto3
from botocore.exceptions import ClientError
from pydantic import TypeAdapter, ValidationError
from pydantic.networks import EmailStr

from flip_api.auth.identity.base import IdentityProvider
from flip_api.auth.identity.errors import (
    IdentityProviderError,
    InvalidIdentifierError,
    UserAlreadyExistsError,
    UserNotFoundError,
)
from flip_api.config import DevSettings, ProdSettings
from flip_api.domain.schemas.users import CognitoUser
from flip_api.utils.cors import normalise_origins
from flip_api.utils.logger import logger

_EMAIL_VALIDATOR: TypeAdapter[str] = TypeAdapter(EmailStr)

boto3.set_stream_logger("boto3.resources", logging.INFO)


def _safe_email_for_filter(email: str) -> str:
    """Validate that ``email`` is safe to interpolate into a Cognito ListUsers Filter expression.

    Cognito's filter syntax delimits values with double quotes; an unescaped
    ``"`` (or backslash) in the value can break out of the quoted context and
    inject additional clauses. EmailStr already forbids the characters that
    would let an attacker do this, but rejecting them explicitly here keeps
    this helper safe to call from any future caller that forgets the
    upstream validation.
    """
    try:
        validated = _EMAIL_VALIDATOR.validate_python(email)
    except ValidationError as exc:
        raise InvalidIdentifierError("Invalid email address format") from exc
    if '"' in validated or "\\" in validated:
        raise InvalidIdentifierError("Invalid email address format")
    return validated


def _safe_uuid_for_filter(user_id: str | UUID) -> str:
    """Coerce ``user_id`` to its canonical string UUID form.

    A valid UUID's string form is hex+hyphen only, so once normalised it can
    be interpolated into Cognito's filter syntax without escaping.
    """
    if isinstance(user_id, UUID):
        return str(user_id)
    try:
        return str(UUID(user_id))
    except (ValueError, AttributeError, TypeError) as exc:
        raise InvalidIdentifierError("Invalid user ID format") from exc


class CognitoIdentityProvider(IdentityProvider):
    """The Cognito user pool behind ``AWS_COGNITO_USER_POOL_ID``.

    Emails and enabled state live only in the pool; FLIP keeps profiles and
    roles keyed by the Cognito ``sub``.
    """

    backend = "cognito"

    def __init__(self, settings: DevSettings | ProdSettings) -> None:
        if settings.AWS_COGNITO_USER_POOL_ID is None or settings.AWS_COGNITO_APP_CLIENT_ID is None:
            raise ValueError("AUTH_BACKEND=cognito requires AWS_COGNITO_USER_POOL_ID and AWS_COGNITO_APP_CLIENT_ID")
        self._region = settings.AWS_REGION
        self._pool_id = settings.AWS_COGNITO_USER_POOL_ID
        self._app_client_id = settings.AWS_COGNITO_APP_CLIENT_ID
        self._client: Any = None

    @property
    def client(self) -> Any:
        """The ``cognito-idp`` client, built once per provider: boto3 clients are
        thread-safe and expensive to construct."""
        if self._client is None:
            self._client = boto3.client("cognito-idp", region_name=self._region)
        return self._client

    # --- directory -------------------------------------------------------------------------

    def _list(self, **params: Any) -> list[CognitoUser]:
        # Log only the pool id, never the params: a ``Filter`` carries an
        # email or other PII, and debug logging must not opt into capturing it.
        logger.debug(f"Listing Cognito users in pool {self._pool_id}")
        # ListUsers returns up to 60 users per page; the paginator walks the
        # whole pool so the admin list and the per-trust XNAT onboarding never
        # silently truncate at page 1.
        paginator = self.client.get_paginator("list_users")
        users: list[CognitoUser] = []
        page_index = 0
        try:
            for page_index, page in enumerate(paginator.paginate(UserPoolId=self._pool_id, **params), start=1):
                for user in page.get("Users", []):
                    attributes = {attr["Name"]: attr["Value"] for attr in user.get("Attributes", [])}
                    users.append(
                        CognitoUser(
                            id=UUID(attributes.get("sub", "")),
                            email=attributes.get("email", user.get("Username", "")),
                            is_disabled=not user.get("Enabled", True),
                        )  # type: ignore[call-arg]
                    )
            return users
        except ClientError as e:
            # boto3 ClientError messages can carry request IDs and ARNs that
            # don't belong in a detail surfaced to API clients. Log the full
            # error server-side (with page+collected so operators can tell a
            # mid-walk throttle from a page-1 hard failure), raise a generic one.
            logger.exception(f"Error getting Cognito users (page={page_index + 1}, collected={len(users)})")
            raise IdentityProviderError("Failed to list users") from e

    def list_users(self) -> list[CognitoUser]:
        return self._list()

    def get_user(self, *, user_id: UUID | str | None = None, email: str | None = None) -> CognitoUser:
        if not email and not user_id:
            logger.error("No user email address or ID provided")
            raise InvalidIdentifierError("No user email address or ID provided")

        if email:
            filter_expr = f'email = "{_safe_email_for_filter(email)}"'
        else:
            assert user_id is not None  # guaranteed by the guard above
            filter_expr = f'sub = "{_safe_uuid_for_filter(user_id)}"'

        users = self._list(Filter=filter_expr, Limit=1)
        if not users:
            logger.warning(f"No user found with email: {email} or ID: {user_id}")
            raise UserNotFoundError(f"User with email: {email} or ID: {user_id} is not registered.")
        return users[0]

    def get_username(self, user_id: UUID | str) -> str:
        try:
            return super().get_username(user_id)
        except UserNotFoundError as exc:
            raise UserNotFoundError(f"User with ID {user_id} is not registered.") from exc

    def set_enabled(self, username: str, enabled: bool) -> None:
        try:
            if enabled:
                self.client.admin_enable_user(UserPoolId=self._pool_id, Username=username)
            else:
                self.client.admin_disable_user(UserPoolId=self._pool_id, Username=username)
            logger.debug(f"User {username} {'enabled' if enabled else 'disabled'}")
        except ClientError as e:
            logger.exception("Error updating user")
            raise IdentityProviderError("Failed to update user") from e

    def create_user(self, email: str, *, suppress_invite: bool = False) -> UUID:
        logger.debug("Attempting to register the user...")
        params: dict[str, Any] = {
            "UserPoolId": self._pool_id,
            "Username": email,
            "UserAttributes": [{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}],
        }
        if suppress_invite:
            params["MessageAction"] = "SUPPRESS"
        try:
            response = self.client.admin_create_user(**params)
        except ClientError as e:
            if e.response["Error"]["Code"] == "UsernameExistsException":
                logger.error(f"User with email {email} already exists")
                raise UserAlreadyExistsError(f"User with email {email} already exists") from e
            logger.exception("Error creating user")
            raise IdentityProviderError("Failed to create user") from e

        logger.debug(f"Response from create user request: {response}")
        # Cognito serialises attributes as strings; materialise `sub` into the
        # UUID the signature advertises so SQLModel never coerces at write time.
        user_id_str = next((attr["Value"] for attr in response["User"]["Attributes"] if attr["Name"] == "sub"), None)
        if not user_id_str:
            raise IdentityProviderError("User created but could not get user ID")
        logger.info("User has been created successfully")
        return UUID(user_id_str)

    def delete_user(self, username: str) -> None:
        logger.debug(f"Attempting to delete user: {username}")
        try:
            self.client.admin_delete_user(UserPoolId=self._pool_id, Username=username)
            logger.info(f"Successfully deleted user: {username}")
        except ClientError as e:
            logger.exception("Error deleting user")
            raise IdentityProviderError("Failed to delete user") from e

    # --- MFA -------------------------------------------------------------------------------

    def _fetch_mfa_enabled(self, username: str) -> bool:
        # A user is MFA-active iff SOFTWARE_TOKEN_MFA is in their
        # UserMFASettingList — Cognito adds it only after the user has both
        # verified a software token and had their preference set Enabled=True.
        try:
            response = self.client.admin_get_user(UserPoolId=self._pool_id, Username=username)
        except ClientError as e:
            logger.exception(f"Error fetching MFA state for user {username}")
            raise IdentityProviderError("Failed to fetch MFA state") from e
        return "SOFTWARE_TOKEN_MFA" in response.get("UserMFASettingList", [])

    def _reset_mfa(self, username: str) -> None:
        # Cognito has no admin API to delete a verified TOTP secret, so
        # clearing the preference is the only server-side handle; the
        # app-layer gate then funnels the user through re-enrolment. The
        # global sign-out revokes refresh tokens so a pre-reset session
        # cannot keep operating.
        logger.debug(f"Attempting to reset MFA for user: {username}")
        try:
            self.client.admin_set_user_mfa_preference(
                UserPoolId=self._pool_id,
                Username=username,
                SoftwareTokenMfaSettings={"Enabled": False, "PreferredMfa": False},
            )
            self.client.admin_user_global_sign_out(UserPoolId=self._pool_id, Username=username)
            logger.info(f"Successfully reset MFA and revoked sessions for user: {username}")
        except ClientError as e:
            logger.exception("Error resetting user MFA")
            raise IdentityProviderError("Failed to reset user MFA") from e

    # --- origins ---------------------------------------------------------------------------

    def allowed_origins(self) -> list[str]:
        """The app client's ``CallbackURLs``, normalised to origins.

        The same Cognito app client that authenticates UI logins already
        enumerates the trusted UI origins per environment (see
        ``deploy/providers/AWS/services.tf``); reusing it keeps "where users
        can sign in" and "where the UI may call this API" in lockstep.
        """
        try:
            response = self.client.describe_user_pool_client(UserPoolId=self._pool_id, ClientId=self._app_client_id)
        except ClientError as e:
            logger.exception("Error reading the app client's callback URLs")
            raise IdentityProviderError("Failed to read the allowed origins") from e
        callback_urls: list[str] = response.get("UserPoolClient", {}).get("CallbackURLs", []) or []
        return normalise_origins(callback_urls)

    # --- operator tooling ------------------------------------------------------------------

    def set_password(self, email: str, password: str) -> None:
        try:
            self.client.admin_set_user_password(
                UserPoolId=self._pool_id, Username=email, Password=password, Permanent=True
            )
        except ClientError as e:
            logger.exception(f"Error setting the password for {email}")
            raise IdentityProviderError("Failed to set password") from e

    def describe_target(self) -> str:
        return f"Cognito user pool {self._pool_id} ({self._region})"
