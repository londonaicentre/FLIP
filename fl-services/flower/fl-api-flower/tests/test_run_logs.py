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

from fl_api.schemas import RunLogs

_SERVERAPP_FAILURE = (
    "INFO: Starting ServerApp\n"
    "ERROR: ServerApp raised an exception\n"
    "Traceback (most recent call last):\n"
    "  File 'app/server_app.py', line 26, in <module>\n"
    "    from flip.flower.strategy import min_clients_from_run_config\n"
    "ImportError: cannot import name 'min_clients_from_run_config'\n"
    "ERROR: Exit Code: 607\n"
)


def test_run_logs_returns_untruncated_log(client, src_root, mock_flwr_run):
    mock_flwr_run(stdout=_SERVERAPP_FAILURE)

    response = client.get("/run_logs/9478652229627629048")

    assert response.status_code == 200
    RunLogs.model_validate(response.json())
    assert response.json() == {
        "run_id": "9478652229627629048",
        "log": _SERVERAPP_FAILURE,
        "truncated": False,
    }


def test_run_logs_uses_show_not_stream(client, src_root, mock_flwr_run):
    # The default `--stream` follows the log forever; only `--show` returns.
    commands = mock_flwr_run(stdout=_SERVERAPP_FAILURE)

    client.get("/run_logs/9478652229627629048")

    assert commands == [["uvx", "flwr", "log", "9478652229627629048", "local", "--show"]]


def test_run_logs_keeps_the_tail_and_flags_truncation(client, src_root, mock_flwr_run, monkeypatch):
    # The dependency-install preamble is the half worth dropping; the cause is at the end.
    monkeypatch.setenv("FLOWER_RUN_LOG_MAX_CHARS", "120")
    preamble = "".join(f"Installed package-{index}\n" for index in range(200))
    mock_flwr_run(stdout=preamble + _SERVERAPP_FAILURE)

    response = client.get("/run_logs/1")

    body = response.json()
    assert response.status_code == 200
    assert body["truncated"] is True
    assert "ERROR: Exit Code: 607" in body["log"]
    assert "Installed package-0" not in body["log"]
    # The cut is advanced to a line boundary, so the tail never opens mid-line.
    assert not body["log"].startswith("nstalled")
    assert len(body["log"]) <= 120


def test_run_logs_redacts_credentials(client, src_root, mock_flwr_run):
    mock_flwr_run(stdout="X-Internal-Service-Key: s3cr3t-value\nERROR: Exit Code: 607\n")

    response = client.get("/run_logs/1")

    assert response.status_code == 200
    assert "s3cr3t-value" not in response.json()["log"]
    assert "ERROR: Exit Code: 607" in response.json()["log"]


def test_run_logs_returns_500_when_flwr_fails(client, src_root, mock_flwr_run):
    mock_flwr_run(returncode=1, stderr="Invalid run_id `1`, exiting")

    response = client.get("/run_logs/1")

    assert response.status_code == 500
    assert "Invalid run_id" in response.json()["detail"]


def test_run_logs_returns_500_when_flwr_times_out(client, src_root, mock_flwr_run):
    # A wedged CLI (unreachable SuperLink) must fail the request, not hang the caller.
    mock_flwr_run(exception=subprocess.TimeoutExpired(cmd="flwr log", timeout=60))

    response = client.get("/run_logs/1")

    assert response.status_code == 500


def test_run_logs_rejects_non_numeric_run_id(client, src_root):
    # Same guard as /abort_run: a non-numeric segment never reaches the `flwr` argv.
    response = client.get("/run_logs/not-a-number")

    assert response.status_code == 422


def test_run_logs_invalid_max_chars_falls_back_to_default(client, src_root, mock_flwr_run, monkeypatch):
    # A broken operator value must not truncate to nothing (or blow up the request).
    monkeypatch.setenv("FLOWER_RUN_LOG_MAX_CHARS", "not-a-number")
    mock_flwr_run(stdout=_SERVERAPP_FAILURE)

    response = client.get("/run_logs/1")

    assert response.status_code == 200
    assert response.json()["log"] == _SERVERAPP_FAILURE
