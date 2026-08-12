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
import stat
from unittest.mock import patch

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

    def test_empty_lines_write_an_empty_file_not_a_leading_blank_line(self, tmp_path):
        """An empty list must not seed a lone newline — parity with trust_kit_lib.write_secure."""
        target = tmp_path / ".env.test"

        write_env_file(target, [])

        assert target.read_text() == ""

    def test_restricts_a_new_file_to_owner_read_write(self, tmp_path):
        target = tmp_path / ".env.test"

        write_env_file(target, ["SECRET=value"])

        assert stat.S_IMODE(target.stat().st_mode) == 0o600

    def test_tightens_an_existing_world_readable_file_before_replacing_its_contents(self, tmp_path):
        """Fresh credentials must not be written while the old permissive mode still applies."""
        target = tmp_path / ".env.test"
        existing_contents = "EXISTING=1\n"
        target.write_text(existing_contents)
        target.chmod(0o644)

        real_fchmod = os.fchmod

        def assert_mode_is_tightened_before_write(fd, mode):
            # ftruncate and the new write happen after fchmod, so this must still be the old
            # content. Reversing the order would expose SECRET=value through mode 0644.
            assert target.read_text() == existing_contents
            real_fchmod(fd, mode)

        with patch("flip_api.scripts.env_utils.os.fchmod", side_effect=assert_mode_is_tightened_before_write):
            write_env_file(target, ["EXISTING=1", "SECRET=value"])

        assert stat.S_IMODE(target.stat().st_mode) == 0o600
