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

"""`make status` runs on the operator's machine, which need not be Linux (FLIP#1390).

Its last section shells out to `free`, which macOS does not ship: the missing binary raised
FileNotFoundError out of run_command and crashed the script after every check had printed.
"""

import importlib.util
from pathlib import Path

CHART_DIR = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_status", CHART_DIR / "check_status.py")
check_status = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_status)


def test_a_missing_binary_is_a_failed_command_not_a_crash():
    ok, output = check_status.run_command(["flip-no-such-binary-1390"])

    assert ok is False
    assert "flip-no-such-binary-1390" in output
