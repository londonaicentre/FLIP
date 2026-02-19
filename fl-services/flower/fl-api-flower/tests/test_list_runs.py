import pytest

from fl_api.schemas import RunRecord


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
    assert len(response.json()) == 2
    for run in response.json():
        RunRecord.model_validate(run)


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
