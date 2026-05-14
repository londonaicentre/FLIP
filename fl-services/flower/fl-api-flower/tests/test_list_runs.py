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
    assert body[0] == {"job_id": "9478652229627629048", "status": "FINISHED"}
    assert body[1] == {"job_id": "2528745119497052892", "status": "RUNNING"}


def test_list_jobs_alias_returns_same_shape(client, src_root, mock_flwr_run):
    mock_flwr_run(stdout='{"success": true, "runs": [{"run-id":"1","fab-name":"x","status":"running"}]}')

    response = client.get("/list_jobs")

    assert response.status_code == 200
    assert response.json() == [{"job_id": "1", "status": "RUNNING"}]


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
