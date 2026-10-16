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

import requests
from fastapi import HTTPException, status

from imaging_api.utils.logger import logger
from imaging_api.utils.xnat_url import XNAT_REQUEST_TIMEOUT

# Bound both calls to XNAT: without a timeout a hung XNAT holds open every request that needs a
# session. The session check is a one-line answer, so it fails fast and falls through to a login;
# the login gets the same bound as imaging-api's other metadata calls to XNAT.
SESSION_CHECK_TIMEOUT_SECONDS = 5
LOGIN_TIMEOUT_SECONDS = XNAT_REQUEST_TIMEOUT


class XnatTokenFactory:
    """
    Factory class for retrieving and caching XNAT authentication tokens.
    """

    def __init__(self, url: str, username: str, password: str) -> None:
        self.url = url
        self.username = username
        self.password = password
        self.token = ""

    def get_xnat_cookie(self) -> str:
        """
        Retrieves a new XNAT authentication token if XNAT no longer accepts the cached one, or none is set.
        Caches the token for reuse.

        Returns:
            str: A valid XNAT session token.

        Raises:
            HTTPException: If there is an error during the authentication process or if the token cannot be retrieved.
        """
        # Check token validity by making a lightweight request to XNAT
        if self.is_token_valid(self.token):
            return self.token

        # Request a new token from XNAT
        session_url = f"{self.url}/data/JSESSION"
        try:
            response = requests.post(session_url, auth=(self.username, self.password), timeout=LOGIN_TIMEOUT_SECONDS)
        except requests.exceptions.Timeout:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=f"Failed to authenticate with XNAT: no response within {LOGIN_TIMEOUT_SECONDS} s",
            ) from None

        if response.status_code != status.HTTP_200_OK:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Failed to authenticate with XNAT: {response.text}",
            )

        token_value = response.text.strip()

        if not token_value:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid authentication response from XNAT: {response.text}",
            )

        self.token = token_value
        return token_value

    def is_token_valid(self, token: str) -> bool:
        """
        Checks if a given XNAT session token is still valid.

        ``GET /xapi/users/username`` needs a live session: XNAT answers 401 for an unknown, expired or
        logged-out one.

        Args:
            token (str): The XNAT session token to validate.

        Returns:
            bool: True if XNAT accepts the session, False otherwise.
        """
        status_url = f"{self.url}/xapi/users/username"
        headers = {"Cookie": f"JSESSIONID={token}"}

        try:
            response = requests.get(status_url, headers=headers, timeout=SESSION_CHECK_TIMEOUT_SECONDS)
        except requests.exceptions.RequestException as e:
            # Treat connection errors as a sign we can't confirm validity right now
            logger.debug(f"XNAT session check failed (connection error), logging in again: {e}")
            return False

        logger.debug(f"XNAT session check returned {response.status_code}")
        return response.status_code == status.HTTP_200_OK
