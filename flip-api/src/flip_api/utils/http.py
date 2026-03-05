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
import ssl
from typing import Any, Dict, Optional, Union

import httpx

from flip_api.utils.logger import logger


def trust_ssl_context() -> Union[ssl.SSLContext, bool]:
    """Return an SSLContext that trusts the Trust CA, or True for default verification.

    Reads `TRUST_CA_BUNDLE` environment variable which should point to a PEM file.
    If not set, returns True to use system CA bundle.

    If `TRUST_CA_BUNDLE` is set but invalid or unreadable, this function raises a
    RuntimeError so that misconfiguration is detected early instead of silently
    falling back to system CAs.
    """
    ca_bundle = os.getenv("TRUST_CA_BUNDLE")
    if ca_bundle:
        try:
            ctx = ssl.create_default_context(cafile=ca_bundle)
            return ctx
        except (OSError, ssl.SSLError) as exc:
            # Fail fast if the configured CA bundle is invalid or cannot be loaded.
            logger.error(
                "Failed to create SSL context with TRUST_CA_BUNDLE '%s': %s",
                ca_bundle,
                repr(exc),
            )
            raise RuntimeError(
                f"Invalid TRUST_CA_BUNDLE configuration: failed to load '{ca_bundle}'"
            ) from exc
    return True


def http_get(url: str, request_id: Optional[str] = None) -> Any:
    """Perform an HTTP GET request to the specified URL with optional request ID for tracing."""
    headers = {"x-request-id": request_id} if request_id else {}
    with httpx.Client(verify=trust_ssl_context()) as client:
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
    url: str, request_id: Optional[str] = None, data: Optional[Dict] = None, timeout: Optional[float] = None
) -> Any:
    """Perform an HTTP POST request to the specified URL with optional request ID for tracing."""
    headers = (
        {"Content-Type": "application/json", "x-request-id": request_id}
        if request_id
        else {"Content-Type": "application/json"}
    )
    with httpx.Client(verify=trust_ssl_context()) as client:
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


def http_delete(url: str, request_id: Optional[str] = None) -> Any:
    """Perform an HTTP DELETE request to the specified URL with optional request ID for tracing."""
    headers = {"x-request-id": request_id} if request_id else {}
    with httpx.Client(verify=trust_ssl_context()) as client:
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
