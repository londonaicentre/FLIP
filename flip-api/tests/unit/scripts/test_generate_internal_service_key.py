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

"""Tests for generate_internal_service_key script."""

import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from flip_api.scripts.generate_internal_service_key import main


@pytest.fixture
def env_file(tmp_path: Path) -> Path:
    """Create a minimal env file with placeholders."""
    f = tmp_path / ".env.test"
    f.write_text(
        "SOME_VAR=hello\n"
        "INTERNAL_SERVICE_KEY_HEADER=X-Internal-Service-Key\n"
        "INTERNAL_SERVICE_KEY_HASH=<placeholder>\n"
    )
    return f


def _parse_env(content: str) -> dict[str, str]:
    """Parse env file content into a dict."""
    return {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in content.strip().splitlines()
        if "=" in line and not line.startswith("#")
    }


class TestGenerateInternalServiceKey:
    """Tests for the generate_internal_service_key script."""

    def test_generates_key_and_hash_in_env_file(self, env_file: Path) -> None:
        """First run writes both INTERNAL_SERVICE_KEY and INTERNAL_SERVICE_KEY_HASH."""
        with patch("sys.argv", ["prog", "--env-file", str(env_file), "--force"]):
            main()

        env = _parse_env(env_file.read_text())
        assert "INTERNAL_SERVICE_KEY" in env
        assert "INTERNAL_SERVICE_KEY_HASH" in env
        assert env["INTERNAL_SERVICE_KEY_HASH"] == hashlib.sha256(env["INTERNAL_SERVICE_KEY"].encode()).hexdigest()

    def test_skips_when_key_and_hash_in_sync(self, env_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """When key and hash already exist and match, script skips without changes."""
        key = "test-key-abc123"
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        env_file.write_text(f"INTERNAL_SERVICE_KEY={key}\nINTERNAL_SERVICE_KEY_HASH={key_hash}\n")

        with patch("sys.argv", ["prog", "--env-file", str(env_file)]):
            main()

        assert f"INTERNAL_SERVICE_KEY={key}" in env_file.read_text()
        assert "in sync" in capsys.readouterr().out.lower()

    def test_resyncs_hash_when_mismatched(self, env_file: Path) -> None:
        """When key exists but hash is wrong, script updates hash to match key."""
        key = "existing-key-xyz"
        env_file.write_text(f"INTERNAL_SERVICE_KEY={key}\nINTERNAL_SERVICE_KEY_HASH=wrong-hash\n")

        with patch("sys.argv", ["prog", "--env-file", str(env_file)]):
            main()

        env = _parse_env(env_file.read_text())
        assert env["INTERNAL_SERVICE_KEY"] == key
        assert env["INTERNAL_SERVICE_KEY_HASH"] == hashlib.sha256(key.encode()).hexdigest()

    def test_force_regenerates_even_when_in_sync(self, env_file: Path) -> None:
        """--force generates a new key even if current key and hash are in sync."""
        old_key = "old-key"
        old_hash = hashlib.sha256(old_key.encode()).hexdigest()
        env_file.write_text(f"INTERNAL_SERVICE_KEY={old_key}\nINTERNAL_SERVICE_KEY_HASH={old_hash}\n")

        with patch("sys.argv", ["prog", "--env-file", str(env_file), "--force"]):
            main()

        env = _parse_env(env_file.read_text())
        assert env["INTERNAL_SERVICE_KEY"] != old_key
        assert env["INTERNAL_SERVICE_KEY_HASH"] == hashlib.sha256(env["INTERNAL_SERVICE_KEY"].encode()).hexdigest()

    def test_adds_key_line_when_missing_from_env(self, tmp_path: Path) -> None:
        """When env file has hash but no key line, script adds the key line."""
        env_file = tmp_path / ".env.test"
        env_file.write_text("INTERNAL_SERVICE_KEY_HASH=stale-hash\n")

        with patch("sys.argv", ["prog", "--env-file", str(env_file), "--force"]):
            main()

        env = _parse_env(env_file.read_text())
        assert "INTERNAL_SERVICE_KEY" in env
        assert env["INTERNAL_SERVICE_KEY_HASH"] == hashlib.sha256(env["INTERNAL_SERVICE_KEY"].encode()).hexdigest()

    def test_does_not_write_key_file(self, env_file: Path, tmp_path: Path) -> None:
        """Script must NOT create deploy/keys/internal_service.key."""
        with patch("sys.argv", ["prog", "--env-file", str(env_file), "--force"]):
            main()

        assert list(tmp_path.rglob("*.key")) == []

    def test_exits_when_env_file_missing(self, tmp_path: Path) -> None:
        """Should exit with code 1 when env file does not exist."""
        missing_file = tmp_path / ".env.nonexistent"
        with patch("sys.argv", ["prog", "--env-file", str(missing_file)]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 1
