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

import json
from unittest.mock import patch

import pytest

from flip_api.scripts.generate_trust_internal_service_keys import main

ENV_TEMPLATE = """\
SOME_VAR=foo
TRUST_INTERNAL_SERVICE_KEY_HEADER=X-Trust-Internal-Service-Key
TRUST_NAMES=["Trust_1", "Trust_2"]
OTHER_VAR=bar
"""


def _parse_env(content: str) -> dict[str, str]:
    """Parse env file content into a dict."""
    return {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in content.strip().splitlines()
        if "=" in line and not line.startswith("#")
    }


class TestGenerateTrustInternalServiceKeys:
    def test_generates_keys(self, tmp_path):
        """main() should write TRUST_INTERNAL_SERVICE_KEYS as a JSON dict keyed by trust name."""
        env_file = tmp_path / ".env.test"
        env_file.write_text(ENV_TEMPLATE)

        with patch("sys.argv", ["generate_trust_internal_service_keys", "--env-file", str(env_file)]):
            main()

        env = _parse_env(env_file.read_text())

        # Unchanged lines preserved
        assert env["SOME_VAR"] == "foo"
        assert env["OTHER_VAR"] == "bar"
        assert env["TRUST_INTERNAL_SERVICE_KEY_HEADER"] == "X-Trust-Internal-Service-Key"

        # TRUST_INTERNAL_SERVICE_KEYS contains both trusts
        keys = json.loads(env["TRUST_INTERNAL_SERVICE_KEYS"])
        assert "Trust_1" in keys
        assert "Trust_2" in keys
        assert len(keys["Trust_1"]) > 0
        assert len(keys["Trust_2"]) > 0
        # Hash dict no longer exists — receivers compare plaintext directly.
        assert "TRUST_INTERNAL_SERVICE_KEY_HASHES" not in env

    def test_exits_when_env_file_missing(self, tmp_path):
        """main() should exit with error when env file does not exist."""
        env_file = tmp_path / ".env.nonexistent"

        with (
            patch("sys.argv", ["generate_trust_internal_service_keys", "--env-file", str(env_file)]),
            pytest.raises(SystemExit, match="1"),
        ):
            main()

    def test_exits_when_no_trust_names_found(self, tmp_path):
        """main() should exit with error when no TRUST_NAMES entry exists."""
        env_file = tmp_path / ".env.test"
        env_file.write_text("SOME_VAR=foo\nOTHER_VAR=bar\n")

        with (
            patch("sys.argv", ["generate_trust_internal_service_keys", "--env-file", str(env_file)]),
            pytest.raises(SystemExit, match="1"),
        ):
            main()

    def test_skips_existing_keys_in_dict(self, tmp_path):
        """main() should preserve existing keys in TRUST_INTERNAL_SERVICE_KEYS and not regenerate them."""
        existing_key = "bLYIayFxl2m_lJ2oGU1ZWhQGSNz7qp41MH6_Ggk3f-o"
        env_file = tmp_path / ".env.test"
        env_file.write_text(
            ENV_TEMPLATE + f'TRUST_INTERNAL_SERVICE_KEYS={{"Trust_1": "{existing_key}"}}\n'
        )

        with patch("sys.argv", ["generate_trust_internal_service_keys", "--env-file", str(env_file)]):
            main()

        env = _parse_env(env_file.read_text())
        keys = json.loads(env["TRUST_INTERNAL_SERVICE_KEYS"])

        # Trust_1 key preserved
        assert keys["Trust_1"] == existing_key

        # Trust_2 key generated
        assert "Trust_2" in keys
        assert keys["Trust_2"] != existing_key

    def test_force_regenerates_all_keys(self, tmp_path):
        """--force should regenerate keys even when TRUST_INTERNAL_SERVICE_KEYS already has values."""
        existing_key = "bLYIayFxl2m_lJ2oGU1ZWhQGSNz7qp41MH6_Ggk3f-o"
        env_file = tmp_path / ".env.test"
        env_file.write_text(
            ENV_TEMPLATE + f'TRUST_INTERNAL_SERVICE_KEYS={{"Trust_1": "{existing_key}"}}\n'
        )

        with patch(
            "sys.argv",
            ["generate_trust_internal_service_keys", "--env-file", str(env_file), "--force"],
        ):
            main()

        env = _parse_env(env_file.read_text())
        keys = json.loads(env["TRUST_INTERNAL_SERVICE_KEYS"])
        assert keys["Trust_1"] != existing_key

    def test_keys_differ_per_trust(self, tmp_path):
        """Each trust must get a distinct key — a leak in one trust must not match another."""
        env_file = tmp_path / ".env.test"
        env_file.write_text(ENV_TEMPLATE)

        with patch("sys.argv", ["generate_trust_internal_service_keys", "--env-file", str(env_file)]):
            main()

        env = _parse_env(env_file.read_text())
        keys = json.loads(env["TRUST_INTERNAL_SERVICE_KEYS"])
        assert keys["Trust_1"] != keys["Trust_2"]

    def test_does_not_write_key_files(self, tmp_path):
        """main() must NOT create any files outside the env file."""
        env_file = tmp_path / ".env.test"
        env_file.write_text(ENV_TEMPLATE)

        with patch("sys.argv", ["generate_trust_internal_service_keys", "--env-file", str(env_file)]):
            main()

        assert list(tmp_path.rglob("*.key")) == []
