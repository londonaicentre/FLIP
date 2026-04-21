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

from flip_api.scripts.env_utils import get_json_value


class TestGetJsonValue:
    def test_returns_value_for_existing_key(self):
        assert get_json_value('{"Trust_1": "abc123"}', "Trust_1") == "abc123"

    def test_returns_empty_string_for_missing_key(self):
        assert get_json_value('{"Trust_1": "abc123"}', "Trust_2") == ""

    def test_returns_empty_string_for_empty_json(self):
        assert get_json_value("", "Trust_1") == ""

    def test_returns_empty_string_for_empty_dict(self):
        assert get_json_value("{}", "Trust_1") == ""
