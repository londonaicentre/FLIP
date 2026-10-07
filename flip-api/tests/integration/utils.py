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

import os

import boto3
from botocore.exceptions import ProfileNotFound

from flip_api.auth.identity.keycloak import password_grant
from flip_api.config import get_settings
from flip_api.utils.constants import ADMIN_EMAIL_1 as ADMIN_EMAIL


def admin_authentication():
    """
    Authenticate as an admin user through the configured identity provider.

    Returns the request headers for the hub. Under ``AUTH_BACKEND=keycloak``
    this is the same OIDC password grant the UI uses, against the public URL
    (so the token's ``iss`` is the one flip-api verifies); the refresh token
    is left in ``FLIP_E2E_REFRESH_TOKEN`` for the smoke's ``_maybe_refresh``.
    Under cognito it is the IAM-gated admin flow, which needs AWS credentials.
    """
    settings = get_settings()
    if settings.AUTH_BACKEND == "keycloak":
        assert settings.KEYCLOAK_PUBLIC_URL is not None
        assert settings.ADMIN_USER_PASSWORD is not None
        tokens = password_grant(
            settings.KEYCLOAK_PUBLIC_URL,
            settings.KEYCLOAK_REALM,
            settings.KEYCLOAK_CLIENT_ID,
            ADMIN_EMAIL,
            settings.ADMIN_USER_PASSWORD.get_secret_value(),
        )
        os.environ.setdefault("FLIP_E2E_REFRESH_TOKEN", tokens.get("refresh_token", ""))
        return {"scheme": "Bearer", "authorization": "Bearer " + tokens["access_token"]}

    # Build a client using an explicit profile when available.
    # This avoids surprises when pytest runs in a different environment.
    region = settings.AWS_REGION
    profile = settings.AWS_PROFILE

    try:
        session = boto3.Session(profile_name=profile)  # loads ~/.aws/config
    except ProfileNotFound:
        # Fall back to env/instance-role creds
        session = boto3.Session()

    client = session.client("cognito-idp", region_name=region)

    try:
        response = client.initiate_auth(
            ClientId=get_settings().AWS_COGNITO_APP_CLIENT_ID,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={
                "USERNAME": ADMIN_EMAIL,
                "PASSWORD": get_settings().ADMIN_USER_PASSWORD.get_secret_value(),
            },
        )

        return {"scheme": "Bearer", "authorization": "Bearer " + response["AuthenticationResult"]["AccessToken"]}

    except client.exceptions.InvalidParameterException as e:
        if "USER_PASSWORD_AUTH" not in str(e):
            raise  # another parameter error (a missing PASSWORD, say): surface it, never return None
        # FLIP's pool never allows USER_PASSWORD_AUTH (deploy/providers/AWS/modules/cognito/main.tf
        # lists USER_AUTH, SRP and ADMIN_USER_PASSWORD only), so the first call always lands here.
        # USER_PASSWORD_AUTH is not enabled on the app client. Try the choice-based USER_AUTH
        # flow next (ALLOW_USER_AUTH, answered with the password): it is a client-side flow,
        # so it works with any AWS credentials — a developer's SSO role has no
        # cognito-idp:AdminInitiateAuth, which the ADMIN_NO_SRP_AUTH fallback below needs.
        try:
            response = client.initiate_auth(
                ClientId=get_settings().AWS_COGNITO_APP_CLIENT_ID,
                AuthFlow="USER_AUTH",
                AuthParameters={
                    "USERNAME": ADMIN_EMAIL,
                    "PREFERRED_CHALLENGE": "PASSWORD",
                    "PASSWORD": get_settings().ADMIN_USER_PASSWORD.get_secret_value(),
                },
            )
            if "AuthenticationResult" in response:
                return {
                    "scheme": "Bearer",
                    "authorization": "Bearer " + response["AuthenticationResult"]["AccessToken"],
                }
        except client.exceptions.InvalidParameterException:
            pass  # USER_AUTH not enabled either; fall through to the admin flow
        # Last resort: ADMIN_INITIATE_AUTH (needs cognito-idp:AdminInitiateAuth on the caller)
        response = client.admin_initiate_auth(
            UserPoolId=get_settings().AWS_COGNITO_USER_POOL_ID,
            ClientId=get_settings().AWS_COGNITO_APP_CLIENT_ID,
            AuthFlow="ADMIN_NO_SRP_AUTH",
            AuthParameters={
                "USERNAME": ADMIN_EMAIL,
                "PASSWORD": get_settings().ADMIN_USER_PASSWORD.get_secret_value(),
            },
        )

        return {
            "scheme": "Bearer",
            "authorization": "Bearer " + response["AuthenticationResult"]["AccessToken"],
        }
