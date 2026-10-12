# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from pathlib import Path

import pytest

from tests.e2e_smoke import parse_args


def test_stop_after_enrichment_without_an_enrichment_command_is_rejected(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        parse_args(["--stop-after-enrichment"])
    assert raised.value.code == 2
    assert "--stop-after-enrichment needs --data-enrichment-cmd" in capsys.readouterr().err


def test_stop_after_enrichment_with_an_enrichment_command_is_accepted(tmp_path: Path) -> None:
    args = parse_args(
        [
            "--stop-after-enrichment",
            "--data-enrichment-cwd",
            str(tmp_path),
            "--data-enrichment-cmd",
            "true",
        ]
    )
    assert args.stop_after_enrichment
    assert args.data_enrichment_cmd == "true"
