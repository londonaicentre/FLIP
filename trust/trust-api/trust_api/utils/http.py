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

import httpx
from fastapi import HTTPException

from trust_api.utils.logger import logger


async def make_request(
    method: str,
    url: str,
    *,
    json_body: dict | None = None,
    params: dict | None = None,
    headers: dict | None = None,
    timeout_seconds: float = 30.0,
    follow_redirects: bool = True,
) -> dict[str, str]:
    """
    Utility function to help make HTTP requests to external APIs.

    Args:
        method (str): HTTP method (GET, POST, etc.)
        url (str): URL of the external API
        json_body (dict | None): Request body content
        params (dict | None): Query parameters for the request
        headers (dict | None): Headers for the request
        timeout_seconds (float): Timeout for the request in seconds
        follow_redirects (bool): Whether to follow redirects

    Returns:
        dict[str, str]: JSON response from the external API

    Raises:
        HTTPException: If there is an error during the request or if the response is not JSON
    """
    params = params or {}
    headers = dict(headers or {})  # copy so we don't mutate callers

    logger.debug(f"Making {method} request to {url} with json_body={json_body}, params={params}, headers={headers}")

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=follow_redirects,
        ) as client:
            resp = await client.request(
                method,
                url,
                json=json_body,
                params=params,
                headers=headers,
            )

        # Raise for HTTP errors, then parse JSON
        if resp.status_code >= 400:
            text_preview = (resp.text or "")[:1000]
            logger.error(f"External API error {resp.status_code} for {method} {url} – body(first1k)={text_preview}")
            raise HTTPException(status_code=resp.status_code, detail=text_preview or "External API error")

        try:
            return resp.json()
        except ValueError:
            text_preview = (resp.text or "")[:1000]
            logger.error(f"Expected JSON from {url}, got non-JSON (first1k)={text_preview}")
            raise HTTPException(status_code=502, detail="External API returned non-JSON response")

    except httpx.RequestError as e:
        # This covers DNS errors, connect timeouts, connection refused, TLS errors, etc.
        cause = repr(getattr(e, "__cause__", None))
        msg = (
            f"{e.__class__.__name__} when calling {method} {getattr(e, 'request', None) and e.request.url}: {e}. "
            f"Cause={cause}"
        )
        logger.error(msg)
        # Map transport layer failures to 502 for upstream callers.
        raise HTTPException(status_code=502, detail=f"Failed to connect to remote service: {e}")
