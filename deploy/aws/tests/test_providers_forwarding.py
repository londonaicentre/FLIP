#  Copyright 2026 London AI Centre
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""`make -C deploy/providers/AWS <goals>` still works for one release after the move to deploy/aws (FLIP#1438)."""

import os
import subprocess
from pathlib import Path

AWS_DIR = Path(__file__).resolve().parents[1]
STUB_DIR = AWS_DIR.parent / "providers" / "AWS"


def _make(directory: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("MAKE")}
    return subprocess.run(
        ["make", "--no-print-directory", "-C", str(directory), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_stub_is_the_only_file_left_at_the_old_path() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", str(STUB_DIR)], capture_output=True, text=True, check=True, cwd=AWS_DIR
    ).stdout.split()
    assert [Path(p).name for p in tracked] == ["GNUmakefile"]


def test_forwards_goals_and_says_where_the_root_went() -> None:
    old = _make(STUB_DIR, "help")
    new = _make(AWS_DIR, "help")
    assert new.returncode == 0, new.stderr
    assert old.returncode == 0, old.stderr
    assert old.stdout == new.stdout
    assert "moved to deploy/aws" in old.stderr


def test_forwards_several_goals_in_one_sub_make() -> None:
    result = _make(STUB_DIR, "-n", "fmt-check", "tflint-lint")
    assert result.returncode == 0, result.stderr
    assert f"-C {AWS_DIR} fmt-check tflint-lint" in result.stdout
    assert _make(AWS_DIR, "-n", "fmt-check", "tflint-lint").stdout in result.stdout


def test_forwards_command_line_variables() -> None:
    # env_mode.mk refuses an unknown HUB at parse time, so the refusal proves the value reached deploy/aws.
    result = _make(STUB_DIR, "help", "HUB=bogus1438")
    assert result.returncode != 0
    assert "got 'bogus1438'" in result.stderr


def test_a_failing_goal_fails_the_forward() -> None:
    result = _make(STUB_DIR, "no-such-target-1438")
    assert result.returncode != 0
    assert "[_forward] Error" in result.stderr
