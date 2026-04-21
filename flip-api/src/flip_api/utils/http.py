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

"""HTTP utilities for internal service-to-service calls on the central hub.

Used exclusively by the FL service to communicate with FL Net API endpoints
(e.g. flip-fl-api-net-1:8000). These are plain HTTP calls between co-located
Docker services — no TLS required.

NOT used for hub↔trust communication, which is handled via the task polling
system (trusts poll the hub; see private_services/trust_tasks.py).
"""

from typing import Any

import httpx

from flip_api.utils.logger import logger


def http_get(url: str, request_id: str | None = None) -> Any:
    """Perform an HTTP GET request to the specified URL with optional request ID for tracing.

    Args:
        url (str): The URL to GET.
        request_id (str | None): Optional value for the ``x-request-id`` header used for
            distributed tracing.

    Returns:
        Any: Parsed JSON body when the response is JSON; otherwise the raw response text.

    Raises:
        httpx.RequestError: If the request cannot be sent (connection, timeout, etc.).
        httpx.HTTPStatusError: If the response status is 4xx/5xx (via ``raise_for_status``).
    """
    headers = {"x-request-id": request_id} if request_id else {}
    with httpx.Client() as client:
        try:
            response = client.get(url, headers=headers)
            response.raise_for_status()
            try:
                return response.json()
            except ValueError:
                return response.text
        except httpx.RequestError as e:
            logger.error(f"HTTP GET failed for {url}: {e}")
            raise


def http_post(
    url: str, request_id: str | None = None, data: dict | None = None, timeout: float | None = None
) -> Any:
    """Perform an HTTP POST request to the specified URL with optional request ID for tracing.

    Args:
        url (str): The URL to POST to.
        request_id (str | None): Optional value for the ``x-request-id`` header used for
            distributed tracing.
        data (dict | None): JSON-serialisable body to send.
        timeout (float | None): Optional request timeout in seconds. When None, httpx defaults
            are used.

    Returns:
        Any: Parsed JSON body when the response is JSON; otherwise the raw response text.

    Raises:
        httpx.RequestError: If the request cannot be sent (connection, timeout, etc.).
        httpx.HTTPStatusError: If the response status is 4xx/5xx (via ``raise_for_status``).
    """
    headers = (
        {"Content-Type": "application/json", "x-request-id": request_id}
        if request_id
        else {"Content-Type": "application/json"}
    )
    with httpx.Client() as client:
        try:
            if timeout is None:
                response = client.post(url, headers=headers, json=data)
            else:
                response = client.post(url, headers=headers, json=data, timeout=timeout)

            response.raise_for_status()
            try:
                return response.json()
            except ValueError:
                return response.text
        except httpx.RequestError as e:
            logger.error(f"HTTP POST failed for {url}: {e}")
            raise


def http_delete(url: str, request_id: str | None = None) -> Any:
    """Perform an HTTP DELETE request to the specified URL with optional request ID for tracing.

    Args:
        url (str): The URL to DELETE.
        request_id (str | None): Optional value for the ``x-request-id`` header used for
            distributed tracing.

    Returns:
        Any: Parsed JSON body when the response is JSON; otherwise the raw response text.

    Raises:
        httpx.RequestError: If the request cannot be sent (connection, timeout, etc.).
        httpx.HTTPStatusError: If the response status is 4xx/5xx (via ``raise_for_status``).
    """
    headers = {"x-request-id": request_id} if request_id else {}
    with httpx.Client() as client:
        try:
            response = client.delete(url, headers=headers)
            response.raise_for_status()
            try:
                return response.json()
            except ValueError:
                return response.text
        except httpx.RequestError as e:
            logger.error(f"HTTP DELETE failed for {url}: {e}")
            raise
