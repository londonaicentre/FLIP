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
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from tomlkit import parse

from fl_api import app as app_module

_NUMPY_RUN_OK = (
    '{"success": true,"run-id":"5489160741982607593",'
    '"fab-id":"flwrlabs/quickstart-numpy","fab-name":"quickstart-numpy",'
    '"fab-version":"1.0.0","fab-hash":"9fcfbb69",'
    '"fab-filename":"flwrlabs.quickstart-numpy.1-0-0.9fcfbb69.fab"}'
)


# --- Production submit: /submit_run + /submit_job are UUID-strict ---


def test_submit_run_with_uuid_success(client, src_root, mock_flwr_run):
    model_id = str(uuid4())
    (src_root / model_id).mkdir(parents=True, exist_ok=True)
    mock_flwr_run(stdout=_NUMPY_RUN_OK)

    response = client.post(f"/submit_run/{model_id}")

    assert response.status_code == 200


def test_submit_job_alias_with_uuid_success(client, src_root, mock_flwr_run):
    """The /submit_job alias (what flip-api calls) behaves identically and is UUID-strict."""
    model_id = str(uuid4())
    (src_root / model_id).mkdir(parents=True, exist_ok=True)
    mock_flwr_run(stdout='{"success": true,"run-id":"123"}')

    response = client.post(f"/submit_job/{model_id}")

    assert response.status_code == 200


@pytest.mark.parametrize("endpoint", ["/submit_run", "/submit_job"])
def test_production_submit_rejects_non_uuid(client, src_root, endpoint):
    """A non-UUID (e.g. a tutorial folder name) is rejected with 400 on the production endpoints."""
    response = client.post(f"{endpoint}/numpy")

    assert response.status_code == 400


# --- Tutorial submit: /submit_tutorial is name-based (still traversal-guarded) ---


def test_submit_tutorial_success(client, src_root, mock_flwr_run):
    (src_root / "numpy").mkdir(parents=True, exist_ok=True)
    mock_flwr_run(stdout=_NUMPY_RUN_OK)

    response = client.post("/submit_tutorial/numpy")

    assert response.status_code == 200


def test_submit_tutorial_merges_flip_job_dir_into_run_config(client, src_root, monkeypatch):
    """submit merges flip-job-dir into a temp run-config file, leaving config.toml untouched."""
    app_dir = src_root / "eval_app" / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    config_toml = app_dir / "config.toml"
    config_toml.write_text('checkpoint = "model.pt"\n')

    captured: dict = {}

    def _capture(command, *_args, **_kwargs):
        captured["command"] = command
        # the temp --run-config file still exists here (before the finally block)
        run_config_arg = command[command.index("--run-config") + 1]
        captured["run_config"] = parse(Path(run_config_arg).read_text())
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

    response = client.post("/submit_tutorial/eval_app")

    assert response.status_code == 200

    run_config = captured["run_config"]
    assert run_config["flip-job-dir"] == str(app_dir)
    assert run_config["checkpoint"] == "model.pt"
    # the app's own config.toml is left untouched
    assert config_toml.read_text() == 'checkpoint = "model.pt"\n'


def test_submit_tutorial_folder_not_found(client, src_root):
    """A valid-but-nonexistent tutorial folder is rejected with 400."""
    response = client.post("/submit_tutorial/numpy")

    assert response.status_code == 400


def test_submit_tutorial_conflict_when_submission_in_progress(client, src_root):
    (src_root / "numpy").mkdir(parents=True, exist_ok=True)
    app_module._submission_in_progress = True

    response = client.post("/submit_tutorial/numpy")

    assert response.status_code == 409


@pytest.mark.parametrize(
    ("exception", "returncode", "stdout", "stderr"),
    [
        (OSError("boom"), 0, "", ""),
        (None, 1, "", "failed"),
        (None, 0, "not-json", ""),
    ],
)
def test_submit_tutorial_execution_failures(
    client,
    src_root,
    mock_flwr_run,
    exception,
    returncode,
    stdout,
    stderr,
):
    (src_root / "numpy").mkdir(parents=True, exist_ok=True)

    if exception is not None:
        mock_flwr_run(exception=exception)
    else:
        mock_flwr_run(returncode=returncode, stdout=stdout, stderr=stderr)

    response = client.post("/submit_tutorial/numpy")

    assert response.status_code == 500


# --- Validator units ---


@pytest.mark.parametrize("bad", ["..", "../etc", "a/b", ".hidden"])
def test_validate_tutorial_folder_rejects_traversal(src_root, bad):
    """Tutorial folder names with traversal sequences or separators are rejected (400)."""
    with pytest.raises(HTTPException) as exc:
        app_module._validate_tutorial_folder(bad)
    assert exc.value.status_code == 400


@pytest.mark.parametrize("bad", ["numpy", "not-a-uuid", "../etc"])
def test_validate_job_folder_rejects_non_uuid(src_root, bad):
    """The production submit validator requires a UUID."""
    with pytest.raises(HTTPException) as exc:
        app_module._validate_job_folder(bad)
    assert exc.value.status_code == 400
