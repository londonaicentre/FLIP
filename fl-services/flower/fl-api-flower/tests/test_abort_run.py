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


def test_abort_run_success(client, src_root, mock_flwr_run):
    mock_flwr_run(returncode=0, stdout=_FLWR_STOP_OK)

    response = client.delete("/abort_run/9478652229627629048")

    assert response.status_code == 200
    JobMetadata.model_validate(response.json())
    assert response.json() == {"job_id": "9478652229627629048", "status": "STOPPED", "status_details": None}


def test_abort_job_alias_returns_same_shape(client, src_root, mock_flwr_run):
    mock_flwr_run(returncode=0, stdout=_FLWR_STOP_OK)

    response = client.delete("/abort_job/9478652229627629048")

    assert response.status_code == 200
    assert response.json() == {"job_id": "9478652229627629048", "status": "STOPPED", "status_details": None}


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
    assert response.json() == {"job_id": "9478652229627629048", "status": "FINISHED", "status_details": None}


def test_abort_run_failure_when_run_not_terminal(client, src_root, mock_flwr_run):
    # `flwr stop` fails and the run is not in the list at all -> genuine failure, 500.
    mock_flwr_run(
        by_command={
            "stop": {"returncode": 1, "stderr": "boom"},
            "list": {"returncode": 0, "stdout": '{"success": true, "runs": []}'},
        }
    )

    response = client.delete("/abort_run/1234567890")

    assert response.status_code == 500


@pytest.mark.parametrize("run_id", ["not-a-number", "-1"])
def test_abort_run_rejects_a_run_id_that_is_not_an_unsigned_integer(client, src_root, mock_flwr_run, run_id):
    # Only an unsigned integer reaches the `flwr` argv (see test_run_logs for the -1 case).
    commands = mock_flwr_run()

    response = client.delete(f"/abort_run/{run_id}")

    assert response.status_code == 422
    assert commands == []


def test_abort_run_failure_when_terminal_run_missing_status(client, src_root, mock_flwr_run):
    # `flwr stop` fails and the matching run in `flwr list` is missing "status" -> the
    # idempotency probe can't confirm terminality, so it falls through to a clean 500.
    mock_flwr_run(
        by_command={
            "stop": {"returncode": 1, "stderr": "boom"},
            "list": {
                "returncode": 0,
                "stdout": '{"success": true, "runs": [{"run-id": "9478652229627629048"}]}',
            },
        }
    )

    response = client.delete("/abort_run/9478652229627629048")

    assert response.status_code == 500


def test_abort_run_failure_when_terminal_run_has_an_unmapped_status(client, src_root, mock_flwr_run):
    # `flwr stop` fails and `flwr list` shows the run with a status this adapter cannot
    # interpret (a future flwr value). UNKNOWN is never proof the run is terminal, so the
    # abort must 500 loudly rather than no-op as though it had succeeded.
    mock_flwr_run(
        by_command={
            "stop": {"returncode": 1, "stderr": "boom"},
            "list": {
                "returncode": 0,
                "stdout": (
                    '{"success": true, "runs": [{"run-id": "9478652229627629048", "status": "some-future-status"}]}'
                ),
            },
        }
    )

    response = client.delete("/abort_run/9478652229627629048")

    assert response.status_code == 500
