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

import subprocess

import pytest

from fl_api import app as app_module


def test_submit_run_success(client, src_root, mock_flwr_run, monkeypatch):
    (src_root / "numpy").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ALLOWED_JOB_FOLDERS", "numpy,3d_spleen_segmentation")
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

    response = client.post("/submit_run/numpy")

    assert response.status_code == 200


def test_submit_run_passes_flip_job_dir_inline(client, src_root, monkeypatch):
    """submit_run passes flip-job-dir as an inline --run-config override, not a file write."""
    app_dir = src_root / "eval_app" / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    config_toml = app_dir / "config.toml"
    config_toml.write_text('checkpoint = "model.pt"\n')

    captured: dict = {}

    def _capture(command, *_args, **_kwargs):
        captured["command"] = command
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=(
                '{"success": true,"run-id":"123","fab-id":"flwrlabs/eval_app",'
                '"fab-name":"eval_app","fab-version":"1.0.0","fab-hash":"abc",'
                '"fab-filename":"flwrlabs.eval_app.1-0-0.abc.fab"}'
            ),
            stderr="",
        )

    monkeypatch.setattr(app_module.subprocess, "run", _capture)

    response = client.post("/submit_run/eval_app")
    assert response.status_code == 200

    command = captured["command"]
    # flip-job-dir is passed inline, pointing at the app directory
    assert f'flip-job-dir="{app_dir}"' in command
    # the researcher's config.toml is still passed as a --run-config file
    assert str(config_toml) in command
    # config.toml itself is not modified -- read-only bind mounts must work
    assert config_toml.read_text() == 'checkpoint = "model.pt"\n'


@pytest.mark.parametrize("app_folder", ["invalid", "numpy"])
def test_submit_run_input_validation(client, src_root, monkeypatch, app_folder):
    monkeypatch.setenv("ALLOWED_JOB_FOLDERS", "numpy,3d_spleen_segmentation")

    response = client.post(f"/submit_run/{app_folder}")

    assert response.status_code == 400


def test_submit_run_conflict_when_submission_in_progress(client, src_root, monkeypatch):
    (src_root / "numpy").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ALLOWED_JOB_FOLDERS", "numpy,3d_spleen_segmentation")
    app_module._submission_in_progress = True

    response = client.post("/submit_run/numpy")

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
    monkeypatch.setenv("ALLOWED_JOB_FOLDERS", "numpy,3d_spleen_segmentation")

    if exception is not None:
        mock_flwr_run(exception=exception)
    else:
        mock_flwr_run(returncode=returncode, stdout=stdout, stderr=stderr)

    response = client.post("/submit_run/numpy")

    assert response.status_code == 500
