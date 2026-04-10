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

"""Tests for flip_api.main rate limit handler."""

import asyncio
from unittest.mock import MagicMock

from flip_api.main import rate_limit_exceeded_handler


class TestRateLimitExceededHandler:
    def test_returns_429_with_detail(self):
        """rate_limit_exceeded_handler should return a 429 JSON response."""
        request = MagicMock()
        exc = MagicMock()

        response = asyncio.get_event_loop().run_until_complete(rate_limit_exceeded_handler(request, exc))

        assert response.status_code == 429
        assert b"Rate limit exceeded" in response.body
