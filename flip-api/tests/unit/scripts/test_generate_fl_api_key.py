# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
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

"""Tests for the generate_fl_api_key script."""

from pathlib import Path
from unittest.mock import patch

import pytest

from flip_api.scripts.generate_fl_api_key import main


def _run(env_file: Path, *extra: str) -> None:
    with patch("sys.argv", ["prog", "--env-file", str(env_file), *extra]):
        main()


def _value(env_file: Path) -> str:
    return next(line.split("=", 1)[1] for line in env_file.read_text().splitlines() if line.startswith("FL_API_KEY="))


def test_mints_a_key_when_empty(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.test"
    env_file.write_text("SOME_VAR=hello\nFL_API_KEY=\n")
    _run(env_file)
    assert len(_value(env_file)) >= 32
    assert "SOME_VAR=hello" in env_file.read_text()


def test_appends_when_absent(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.test"
    env_file.write_text("SOME_VAR=hello\n")
    _run(env_file)
    assert _value(env_file)


def test_keeps_an_existing_key(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env_file = tmp_path / ".env.test"
    env_file.write_text("FL_API_KEY=keep-me\n")
    _run(env_file)
    assert _value(env_file) == "keep-me"
    assert "already set" in capsys.readouterr().out


def test_force_rotates(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.test"
    env_file.write_text("FL_API_KEY=old\n")
    _run(env_file, "--force")
    assert _value(env_file) != "old"


def test_missing_env_file_exits(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        _run(tmp_path / "absent.env")
