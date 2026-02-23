# Copyright (c) 2026 Flower Labs GmbH
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

import pytest

from fl_api.schemas import FlowerCommandResponse


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr", "expected_status"),
    [
        (
            0,
            '{"success": true, "run-id": "9478652229627629048", "status": "stopped"}',
            "",
            200,
        ),
        (1, "", "failed", 500),
        (0, "not-json", "", 500),
    ],
)
def test_abort_run_success_and_failures(
    client,
    src_root,
    mock_flwr_run,
    returncode,
    stdout,
    stderr,
    expected_status,
):
    mock_flwr_run(returncode=returncode, stdout=stdout, stderr=stderr)

    response = client.delete("/abort_run/9478652229627629048")

    assert response.status_code == expected_status
    if expected_status == 200:
        FlowerCommandResponse.model_validate(response.json())
