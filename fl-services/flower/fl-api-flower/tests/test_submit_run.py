import pytest

from fl_api import app as app_module
from fl_api.schemas import FlowerCommandResponse


def test_submit_run_success(client, src_root, mock_flwr_run, monkeypatch):
    (src_root / "numpy").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ALLOWED_JOB_FOLDERS", "numpy,monai")
    mock_flwr_run(
        stdout=(
            "{"
            '"success": true,'
            '"run-id":"5489160741982607593",'
            '"fab-id":"flwrlabs/quickstart-numpy",'
            '"fab-name":"quickstart-numpy",'
            '"fab-version":"1.0.0",'
            '"fab-hash":"9fcfbb69",'
            '"fab-filename":"flwrlabs.quickstart-numpy.1-0-0.9fcfbb69.fab"'
            "}"
        )
    )

    response = client.post("/submit_run", params={"app_folder": "numpy"})

    assert response.status_code == 200
    FlowerCommandResponse.model_validate(response.json())


@pytest.mark.parametrize("app_folder", ["invalid", "numpy"])
def test_submit_run_input_validation(client, src_root, monkeypatch, app_folder):
    monkeypatch.setenv("ALLOWED_JOB_FOLDERS", "numpy,monai")

    response = client.post("/submit_run", params={"app_folder": app_folder})

    assert response.status_code == 400


def test_submit_run_conflict_when_submission_in_progress(client, src_root, monkeypatch):
    (src_root / "numpy").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ALLOWED_JOB_FOLDERS", "numpy,monai")
    app_module._submission_in_progress = True

    response = client.post("/submit_run", params={"app_folder": "numpy"})

    assert response.status_code == 409


@pytest.mark.parametrize(
    ("exception", "returncode", "stdout", "stderr"),
    [
        (OSError("boom"), 0, "", ""),
        (None, 1, "", "failed"),
        (None, 0, "not-json", ""),
    ],
)
def test_submit_run_execution_failures(
    client,
    src_root,
    mock_flwr_run,
    monkeypatch,
    exception,
    returncode,
    stdout,
    stderr,
):
    (src_root / "numpy").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ALLOWED_JOB_FOLDERS", "numpy,monai")

    if exception is not None:
        mock_flwr_run(exception=exception)
    else:
        mock_flwr_run(returncode=returncode, stdout=stdout, stderr=stderr)

    response = client.post("/submit_run", params={"app_folder": "numpy"})

    assert response.status_code == 500
