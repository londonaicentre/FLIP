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

import stat

from flip_api.scripts.env_utils import get_json_value, write_env_file


class TestGetJsonValue:
    def test_returns_value_for_existing_key(self):
        assert get_json_value('{"Trust_1": "abc123"}', "Trust_1") == "abc123"

    def test_returns_empty_string_for_missing_key(self):
        assert get_json_value('{"Trust_1": "abc123"}', "Trust_2") == ""

    def test_returns_empty_string_for_empty_json(self):
        assert get_json_value("", "Trust_1") == ""

    def test_returns_empty_string_for_empty_dict(self):
        assert get_json_value("{}", "Trust_1") == ""


class TestWriteEnvFile:
    def test_writes_lines_with_a_trailing_newline(self, tmp_path):
        target = tmp_path / ".env.test"

        write_env_file(target, ["A=1", "B=2"])

        assert target.read_text() == "A=1\nB=2\n"

    def test_restricts_a_new_file_to_owner_read_write(self, tmp_path):
        target = tmp_path / ".env.test"

        write_env_file(target, ["SECRET=value"])

        assert stat.S_IMODE(target.stat().st_mode) == 0o600

    def test_tightens_an_existing_world_readable_file(self, tmp_path):
        """The chmod must follow the write, not just apply at creation.

        Kit files are usually created by another path and only *updated* here, so a file that
        already exists as 0644 has to be tightened rather than left as the umask made it.
        """
        target = tmp_path / ".env.test"
        target.write_text("EXISTING=1\n")
        target.chmod(0o644)

        write_env_file(target, ["EXISTING=1", "SECRET=value"])

        assert stat.S_IMODE(target.stat().st_mode) == 0o600
