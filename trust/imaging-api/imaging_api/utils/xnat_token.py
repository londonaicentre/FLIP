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

import datetime
from typing import Any

import requests
from fastapi import HTTPException, status

from imaging_api.utils.logger import logger


class XnatTokenFactory:
    """
    Factory class for retrieving and caching XNAT authentication tokens.
    """

    def __init__(self, url: str, username: str, password: str, expiry_hours: int = 24):
        self.url = url
        self.username = username
        self.password = password
        self.expiry_hours = expiry_hours
        self.xnat_cookie: dict[str, Any] = {}

    def get_xnat_cookie(self) -> str:
        """
        Retrieves a new XNAT authentication token if expired or not set.
        Caches the token for reuse.
        """
        # Check token validity by making a lightweight request to XNAT
        if self.is_token_valid(self.xnat_cookie.get("token", "")):
            return self.xnat_cookie["token"]

        # Request a new token from XNAT
        session_url = f"{self.url}/data/JSESSION"
        response = requests.post(session_url, auth=(self.username, self.password))

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

        # Store the token with a timestamp
        self.xnat_cookie = {
            "token": token_value,
            "created_at": datetime.datetime.now(datetime.UTC),
        }

        return token_value

    def is_token_valid(self, token: str) -> bool:
        """
        Checks if a given XNAT session token is still valid.

        Args:
            token (str): The XNAT session token to validate.

        Returns:
            bool: True if the token is valid, False otherwise.
        """
        # Lightweight way to check auth.
        status_url = f"{self.url}/xapi/users"
        headers = {"Cookie": f"JSESSIONID={token}"}

        try:
            response = requests.get(status_url, headers=headers, timeout=5)
            # A 200 OK means the token is valid and the user's status was returned.
            if response.status_code == status.HTTP_200_OK:
                logger.debug(f"Token validation successful for {token[:10]}...")
                return True
            else:
                logger.debug(f"⚠️ Token {token[:10]}... invalid. Status: {response.status_code}")
                return False

        except requests.exceptions.RequestException as e:
            logger.debug(f"❌ XNAT Token validation failed (connection error): {e}")
            # Treat connection errors as a sign we can't confirm validity right now
            return False
