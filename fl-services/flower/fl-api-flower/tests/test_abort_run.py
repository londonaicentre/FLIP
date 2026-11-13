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

from fl_api.schemas import JobMetadata

# `flwr stop --format json` emits {"success": true, "run-id": ...} on success — there is
# no status field, so a returncode-0 stop maps to STOPPED unconditionally.
_FLWR_STOP_OK = '{"success": true, "run-id": "9478652229627629048"}'


@pytest.mark.parametrize("endpoint", ["/abort_run", "/abort_job"])
def test_abort_run_success(client, src_root, mock_flwr_run, endpoint):
    mock_flwr_run(returncode=0, stdout=_FLWR_STOP_OK)

    response = client.delete(f"{endpoint}/9478652229627629048")

    assert response.status_code == 200
    JobMetadata.model_validate(response.json())
    assert response.json() == {"job_id": "9478652229627629048", "status": "STOPPED"}


def test_abort_run_idempotent_for_terminal_run(client, src_root, mock_flwr_run):
    # `flwr stop` fails because the run is already finished; the adapter must fall back
    # to `flwr list`, see the run is terminal, and return its terminal status (200, not 500).
    mock_flwr_run(
        by_command={
            "stop": {"returncode": 1, "stderr": "run already finished"},
            "list": {
                "returncode": 0,
                "stdout": (
                    '{"success": true, "runs": [{"run-id": "9478652229627629048", "status": "finished:completed"}]}'
                ),
            },
        }
    )

    response = client.delete("/abort_run/9478652229627629048")

    assert response.status_code == 200
    assert response.json() == {"job_id": "9478652229627629048", "status": "FINISHED"}


@pytest.mark.parametrize(
    ("run_id", "runs"),
    [
        # The run is not in `flwr list` at all -> genuine failure.
        pytest.param("1234567890", "[]", id="not-listed"),
        # The matching run is missing "status" -> the idempotency probe can't confirm terminality.
        pytest.param("9478652229627629048", '[{"run-id": "9478652229627629048"}]', id="missing-status"),
    ],
)
def test_abort_run_failure_when_run_not_confirmed_terminal(client, src_root, mock_flwr_run, run_id, runs):
    # `flwr stop` fails and the run cannot be shown terminal -> a clean 500, not an idempotent 200.
    mock_flwr_run(
        by_command={
            "stop": {"returncode": 1, "stderr": "boom"},
            "list": {"returncode": 0, "stdout": f'{{"success": true, "runs": {runs}}}'},
        }
    )

    response = client.delete(f"/abort_run/{run_id}")

    assert response.status_code == 500


def test_abort_run_rejects_non_numeric_run_id(client, src_root):
    # Flower run ids are integers, so a non-numeric path segment is rejected by FastAPI
    # (422) before it can reach the `flwr stop` command line.
    response = client.delete("/abort_run/not-a-number")

    assert response.status_code == 422
