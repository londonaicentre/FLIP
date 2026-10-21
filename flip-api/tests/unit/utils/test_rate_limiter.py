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

import hashlib
from unittest.mock import MagicMock

from flip_api.config import get_settings
from flip_api.utils.rate_limiter import _trust_key


# `request.headers` must be a real dict on these mocks: MagicMock's `.get(...)` returns
# another MagicMock (truthy), which would force every test through the API-key branch
# and try to hash a non-bytes object.
class TestTrustKey:
    def test_hashes_trust_api_key_when_header_present(self):
        """Primary path: rate-limit per-trust by hashing the trust API key header."""
        api_key = "trust-1-secret"  # pragma: allowlist secret
        request = MagicMock()
        request.headers = {get_settings().TRUST_API_KEY_HEADER: api_key}

        result = _trust_key(request)

        expected = "trust:" + hashlib.sha256(api_key.encode()).hexdigest()[:16]
        assert result == expected

    def test_falls_back_to_client_host_without_api_key(self):
        """Fallback: client.host when the API key header is absent."""
        request = MagicMock()
        request.headers = {}
        request.client.host = "10.0.0.42"

        result = _trust_key(request)

        assert result == "10.0.0.42"

    def test_returns_unknown_when_no_client(self):
        """Fallback: 'unknown' sentinel when there's no client and nothing else identifies the caller."""
        request = MagicMock()
        request.headers = {}
        request.client = None

        result = _trust_key(request)

        assert result == "unknown"
