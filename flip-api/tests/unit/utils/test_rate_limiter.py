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

from unittest.mock import MagicMock

from flip_api.utils.rate_limiter import _trust_name_key


class TestTrustNameKey:
    def test_returns_trust_name_when_present_in_path_params(self):
        """Should return the trust_name path parameter when it exists."""
        request = MagicMock()
        request.path_params = {"trust_name": "Trust_1"}
        request.client.host = "192.168.1.1"

        result = _trust_name_key(request)

        assert result == "Trust_1"

    def test_falls_back_to_client_host_when_no_trust_name(self):
        """Should return request.client.host when trust_name is not in path_params."""
        request = MagicMock()
        request.path_params = {}
        request.client.host = "10.0.0.42"

        result = _trust_name_key(request)

        assert result == "10.0.0.42"

    def test_returns_unknown_when_no_client(self):
        """Should return 'unknown' when request.client is None and no trust_name."""
        request = MagicMock()
        request.path_params = {}
        request.client = None

        result = _trust_name_key(request)

        assert result == "unknown"
