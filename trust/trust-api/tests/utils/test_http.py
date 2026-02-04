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

from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from fastapi import HTTPException

from trust_api.utils.http import make_request


# Mock httpx.AsyncClient
@pytest.mark.asyncio
@patch("trust_api.utils.http.httpx.AsyncClient")
async def test_make_request_success(mock_client):
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.json = Mock(return_value={"message": "success"})
    mock_client.return_value.__aenter__.return_value.request.return_value = mock_response

    result = await make_request("GET", "http://example.com")
    assert result == {"message": "success"}


@pytest.mark.asyncio
@patch("trust_api.utils.http.httpx.AsyncClient")
async def test_make_request_request_error(mock_client):
    # Simulate a transport-layer error (DNS/connect timeout/etc.)
    req = httpx.Request("GET", "http://example.com")
    mock_client.return_value.__aenter__.return_value.request.side_effect = httpx.RequestError(
        "Connection failed", request=req
    )

    with pytest.raises(HTTPException) as exc_info:
        await make_request("GET", "http://example.com")

    assert exc_info.value.status_code == 502
    assert "Failed to connect to remote service:" in exc_info.value.detail


@pytest.mark.asyncio
@patch("trust_api.utils.http.httpx.AsyncClient")
async def test_make_request_http_status_error(mock_client):
    # No exception raised by client.request; instead we get a 4xx response.
    mock_response = AsyncMock()
    mock_response.status_code = 404
    mock_response.text = "Not Found"
    # json() won't be called because status_code >= 400 triggers early raise.
    mock_client.return_value.__aenter__.return_value.request.return_value = mock_response

    with pytest.raises(HTTPException) as exc_info:
        await make_request("GET", "http://example.com")

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Not Found"
