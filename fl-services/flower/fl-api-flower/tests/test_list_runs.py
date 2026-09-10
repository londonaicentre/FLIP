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


def test_list_runs_success(client, src_root, mock_flwr_run):
    mock_flwr_run(
        stdout=(
            "{"
            '"success": true,'
            '"runs": ['
            '{"run-id":"9478652229627629048","fab-name":"quickstart-numpy","status":"finished:completed"},'
            '{"run-id":"2528745119497052892","fab-name":"quickstart-numpy","status":"running"}'
            "]"
            "}"
        )
    )

    response = client.get("/list_runs")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    for item in body:
        JobMetadata.model_validate(item)
    assert body[0] == {"job_id": "9478652229627629048", "status": "FINISHED", "status_details": None}
    assert body[1] == {"job_id": "2528745119497052892", "status": "RUNNING", "status_details": None}


def test_list_jobs_alias_returns_same_shape(client, src_root, mock_flwr_run):
    mock_flwr_run(stdout='{"success": true, "runs": [{"run-id":"1","fab-name":"x","status":"running"}]}')

    response = client.get("/list_jobs")

    assert response.status_code == 200
    assert response.json() == [{"job_id": "1", "status": "RUNNING", "status_details": None}]


def test_status_details_carries_the_backends_own_cause(client, src_root, mock_flwr_run):
    # `flwr ls` already explains a failed run in one line. Carrying it costs nothing —
    # no extra call, no log fetch — and it is the cause the hub puts at the top of the
    # model's activity feed (FLIP#1001).
    mock_flwr_run(
        stdout=(
            '{"success": true, "runs": [{"run-id":"7","fab-name":"x","status":"finished:failed",'
            '"status-details":"ServerApp failed with exception: No module named \'flwr.common.message\'"}]}'
        )
    )

    response = client.get("/list_runs")

    assert response.status_code == 200
    assert response.json() == [
        {
            "job_id": "7",
            "status": "FAILED",
            "status_details": "ServerApp failed with exception: No module named 'flwr.common.message'",
        }
    ]


def test_status_details_na_is_reported_as_absent(client, src_root, mock_flwr_run):
    # flwr writes the literal "N/A" for a run with nothing to say rather than omitting the
    # key; passing it through would print "Reported cause: N/A" in the activity feed.
    mock_flwr_run(
        stdout='{"success": true, "runs": [{"run-id":"7","fab-name":"x","status":"running","status-details":"N/A"}]}'
    )

    assert client.get("/list_runs").json() == [{"job_id": "7", "status": "RUNNING", "status_details": None}]


def test_status_details_is_collapsed_redacted_and_bounded(client, src_root, mock_flwr_run):
    # The text is a researcher-authored exception message from a container holding a hub
    # service key, so it gets the same masking the run log does — and a length bound, since
    # nothing upstream constrains how long an exception message can be.
    secret = "aws_secret_access_key=" + "b" * 900
    mock_flwr_run(
        stdout=(
            '{"success": true, "runs": [{"run-id":"7","fab-name":"x","status":"finished:failed",'
            f'"status-details":"boom\\n   over  lines {secret}"}}]}}'
        )
    )

    details = client.get("/list_runs").json()[0]["status_details"]

    assert details is not None
    assert "\n" not in details
    assert "boom over lines" in details
    assert "b" * 40 not in details
    assert len(details) <= 500


def test_status_details_absent_key_is_not_an_error(client, src_root, mock_flwr_run):
    # Older flwr versions have no such key. Unlike run-id/status (which 500 when missing),
    # this one is simply absent detail.
    mock_flwr_run(stdout='{"success": true, "runs": [{"run-id":"7","fab-name":"x","status":"running"}]}')

    assert client.get("/list_runs").json() == [{"job_id": "7", "status": "RUNNING", "status_details": None}]


def test_list_runs_malformed_run_returns_500(client, src_root, mock_flwr_run):
    # A run dict missing "run-id" must fail cleanly with a 500, not an opaque KeyError.
    mock_flwr_run(stdout='{"success": true, "runs": [{"status": "running"}]}')

    response = client.get("/list_runs")

    assert response.status_code == 500


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr"),
    [
        (1, "", "failed"),
        (0, "not-json", ""),
        (0, '{"success": true}', ""),
    ],
)
def test_list_runs_failures(client, src_root, mock_flwr_run, returncode, stdout, stderr):
    mock_flwr_run(returncode=returncode, stdout=stdout, stderr=stderr)

    response = client.get("/list_runs")

    assert response.status_code == 500
