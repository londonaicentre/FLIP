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

    response = client.delete("/abort_run", params={"run_id": "9478652229627629048"})

    assert response.status_code == expected_status
    if expected_status == 200:
        FlowerCommandResponse.model_validate(response.json())
