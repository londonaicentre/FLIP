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
import json
from unittest.mock import patch

import pytest

from flip_api.scripts.generate_trust_api_keys import main

ENV_TEMPLATE = """\
SOME_VAR=foo
TRUST_API_KEY_HEADER=Authorization
TRUST_NAMES=["Trust_1", "Trust_2"]
TRUST_API_KEY_HASHES={{"Trust_1": "<hash1>", "Trust_2": "<hash2>"}}
OTHER_VAR=bar
"""


def _parse_env(content: str) -> dict[str, str]:
    """Parse env file content into a dict."""
    return {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in content.strip().splitlines()
        if "=" in line and not line.startswith("#")
    }


class TestGenerateTrustApiKeys:
    def test_generates_keys_and_updates_hashes(self, tmp_path):
        """main() should write TRUST_API_KEYS and TRUST_API_KEY_HASHES as JSON dicts."""
        env_file = tmp_path / ".env.test"
        env_file.write_text(ENV_TEMPLATE)

        with patch("sys.argv", ["generate_trust_api_keys", "--env-file", str(env_file)]):
            main()

        env = _parse_env(env_file.read_text())

        # Unchanged lines preserved
        assert env["SOME_VAR"] == "foo"
        assert env["OTHER_VAR"] == "bar"
        assert env["TRUST_API_KEY_HEADER"] == "Authorization"

        # TRUST_API_KEYS contains both trusts
        keys = json.loads(env["TRUST_API_KEYS"])
        assert "Trust_1" in keys
        assert "Trust_2" in keys
        assert len(keys["Trust_1"]) > 0
        assert len(keys["Trust_2"]) > 0

        # Hashes match keys
        hashes = json.loads(env["TRUST_API_KEY_HASHES"])
        assert hashes["Trust_1"] == hashlib.sha256(keys["Trust_1"].encode()).hexdigest()
        assert hashes["Trust_2"] == hashlib.sha256(keys["Trust_2"].encode()).hexdigest()

    def test_exits_when_env_file_missing(self, tmp_path):
        """main() should exit with error when env file does not exist."""
        env_file = tmp_path / ".env.nonexistent"

        with (
            patch("sys.argv", ["generate_trust_api_keys", "--env-file", str(env_file)]),
            pytest.raises(SystemExit, match="1"),
        ):
            main()

    def test_exits_when_no_trust_names_found(self, tmp_path):
        """main() should exit with error when no TRUST_NAMES entry exists."""
        env_file = tmp_path / ".env.test"
        env_file.write_text("SOME_VAR=foo\nOTHER_VAR=bar\n")

        with (
            patch("sys.argv", ["generate_trust_api_keys", "--env-file", str(env_file)]),
            pytest.raises(SystemExit, match="1"),
        ):
            main()

    def test_skips_existing_keys_in_dict(self, tmp_path):
        """main() should preserve existing keys in TRUST_API_KEYS and not regenerate them."""
        existing_key = "bLYIayFxl2m_lJ2oGU1ZWhQGSNz7qp41MH6_Ggk3f-o"
        env_file = tmp_path / ".env.test"
        env_file.write_text(
            ENV_TEMPLATE + f'TRUST_API_KEYS={{"Trust_1": "{existing_key}"}}\n'
        )

        with patch("sys.argv", ["generate_trust_api_keys", "--env-file", str(env_file)]):
            main()

        env = _parse_env(env_file.read_text())
        keys = json.loads(env["TRUST_API_KEYS"])

        # Trust_1 key preserved
        assert keys["Trust_1"] == existing_key

        # Trust_2 key generated
        assert "Trust_2" in keys
        assert keys["Trust_2"] != existing_key

        # Hashes updated for both trusts
        hashes = json.loads(env["TRUST_API_KEY_HASHES"])
        assert len(hashes) == 2
        assert hashes["Trust_1"] == hashlib.sha256(existing_key.encode()).hexdigest()

    def test_force_regenerates_all_keys(self, tmp_path):
        """--force should regenerate keys even when TRUST_API_KEYS already has values."""
        existing_key = "bLYIayFxl2m_lJ2oGU1ZWhQGSNz7qp41MH6_Ggk3f-o"
        env_file = tmp_path / ".env.test"
        env_file.write_text(
            ENV_TEMPLATE + f'TRUST_API_KEYS={{"Trust_1": "{existing_key}"}}\n'
        )

        with patch("sys.argv", ["generate_trust_api_keys", "--env-file", str(env_file), "--force"]):
            main()

        env = _parse_env(env_file.read_text())
        keys = json.loads(env["TRUST_API_KEYS"])
        assert keys["Trust_1"] != existing_key

    def test_does_not_write_key_files(self, tmp_path):
        """main() must NOT create trust/trust-keys/*.key files."""
        env_file = tmp_path / ".env.test"
        env_file.write_text(ENV_TEMPLATE)

        with patch("sys.argv", ["generate_trust_api_keys", "--env-file", str(env_file)]):
            main()

        assert list(tmp_path.rglob("*.key")) == []
