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

from typing import Any, Dict
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from flip_api.config import get_settings
from flip_api.utils.logger import logger

security = HTTPBearer()
AWS_REGION = get_settings().AWS_REGION
USER_POOL_ID = get_settings().AWS_COGNITO_USER_POOL_ID


def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> UUID:
    """
    Uses PyJWT and PyJWKClient to verify credential token with AWS Cognito

    Args:
        credentials: HTTPAuthorizationCredentials
            The HTTP authorization credentials in the result of using `HTTPBearer` or
            `HTTPDigest` in a dependency.

    Returns:
        UUID: The user ID (sub claim) from the verified token

    Raises:
        HTTPException: If token is invalid, expired, or verification fails
    """
    token = credentials.credentials

    try:
        # Get the JWK (JSON Web Key) from Cognito
        jwks_url = f"https://cognito-idp.{AWS_REGION}.amazonaws.com/{USER_POOL_ID}/.well-known/jwks.json"
        jwks_client = PyJWKClient(jwks_url)

        # Get the signing key from the JWT header
        signing_key = jwks_client.get_signing_key_from_jwt(token)

        # Decode and verify the token
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=None,  # Cognito tokens don't typically have audience validation
            options={"verify_aud": False},  # Disable audience verification for Cognito
        )

        # Verify token type and usage
        if payload.get("token_use") not in ["id", "access"]:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type. Expected ID token.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Extract user ID from the 'sub' claim
        user_id_str = payload.get("sub")
        if not user_id_str:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token missing user identifier",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Convert to UUID
        try:
            user_id = UUID(user_id_str)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid user identifier format",
                headers={"WWW-Authenticate": "Bearer"},
            )

        logger.info(f"Token verified successfully for user: {user_id}")
        return user_id

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as e:
        logger.error(f"Token validation failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as e:
        logger.error(f"Unexpected error during token verification: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error during authentication",
        )


def get_token_payload(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Dict[str, Any]:
    """
    Alternative function to get the full token payload if needed elsewhere.

    Args:
        credentials: HTTPAuthorizationCredentials from the Bearer token

    Returns:
        Dict[str, Any]: The decoded JWT payload
    """
    token = credentials.credentials

    try:
        jwks_url = f"https://cognito-idp.{AWS_REGION}.amazonaws.com/{USER_POOL_ID}/.well-known/jwks.json"
        jwks_client = PyJWKClient(jwks_url)
        signing_key = jwks_client.get_signing_key_from_jwt(token)

        # TODO Review -- see above call with argument 'audience=None'
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )

        return payload

    except Exception as e:
        logger.error(f"Failed to decode token payload: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
