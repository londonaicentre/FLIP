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
    # Exactly the last whole lines that fit in 120 chars: the raw 120-char window opens
    # mid-way through the `from flip.flower...` line, and the cut is advanced past it.
    assert body["log"] == ("ImportError: cannot import name 'min_clients_from_run_config'\nERROR: Exit Code: 607\n")


def test_run_logs_tail_keeps_a_line_that_ends_exactly_at_the_cut(client, src_root, mock_flwr_run, monkeypatch):
    # When the window opens exactly on a line boundary there is no partial line to drop;
    # advancing anyway would throw away a whole intact line.
    monkeypatch.setenv("FLOWER_RUN_LOG_MAX_CHARS", "12")
    mock_flwr_run(stdout="line1\nline2\nline3\n")

    response = client.get("/run_logs/1")

    assert response.json() == {"run_id": "1", "log": "line2\nline3\n", "truncated": True}


def test_run_logs_tail_of_a_single_giant_line_is_never_empty(client, src_root, mock_flwr_run, monkeypatch):
    # The only newline inside the window is its last character (one over-long line, e.g.
    # the per-run `Installed: [...]` dump, then a newline). Advancing past it would return
    # "" with truncated=True, and the hub would report a log that exists as unretrievable.
    monkeypatch.setenv("FLOWER_RUN_LOG_MAX_CHARS", "50")
    mock_flwr_run(stdout="x" * 200 + "\n")

    response = client.get("/run_logs/1")

    assert response.json() == {"run_id": "1", "log": "x" * 49 + "\n", "truncated": True}


def test_run_logs_tail_with_no_newline_is_the_raw_window(client, src_root, mock_flwr_run, monkeypatch):
    monkeypatch.setenv("FLOWER_RUN_LOG_MAX_CHARS", "50")
    mock_flwr_run(stdout="x" * 200)

    response = client.get("/run_logs/1")

    assert response.json() == {"run_id": "1", "log": "x" * 50, "truncated": True}


def test_run_logs_redacts_credentials(client, src_root, mock_flwr_run):
    mock_flwr_run(stdout="X-Internal-Service-Key: s3cr3t-value\nERROR: Exit Code: 607\n")

    response = client.get("/run_logs/1")

    assert response.status_code == 200
    assert "s3cr3t-value" not in response.json()["log"]
    assert "ERROR: Exit Code: 607" in response.json()["log"]


def test_run_logs_redacts_before_truncating(client, src_root, mock_flwr_run, monkeypatch):
    # A cut landing inside `key: value` must not strip the keyword the matcher needs:
    # tail-then-redact would leave "ey: s3cr3t-value", redact-then-tail cannot.
    monkeypatch.setenv("FLOWER_RUN_LOG_MAX_CHARS", "16")
    mock_flwr_run(stdout="X-Internal-Service-Key: s3cr3t-value")

    response = client.get("/run_logs/1")

    assert response.status_code == 200
    assert "s3cr3t" not in response.json()["log"]


def test_run_logs_returns_500_when_flwr_fails(client, src_root, mock_flwr_run):
    # A gRPC error other than NOT_FOUND / DEADLINE_EXCEEDED is re-raised by `flwr log` and
    # exits non-zero with a traceback on stderr.
    mock_flwr_run(returncode=1, stderr="grpc._channel._MultiThreadedRendezvous: StatusCode.UNAVAILABLE")

    response = client.get("/run_logs/1")

    assert response.status_code == 500
    assert "UNAVAILABLE" in response.json()["detail"]


def test_run_logs_500_detail_is_redacted(client, src_root, mock_flwr_run):
    mock_flwr_run(returncode=1, stderr="failed: token=abc123 refused")

    response = client.get("/run_logs/1")

    assert response.status_code == 500
    assert "abc123" not in response.json()["detail"]


def test_run_logs_returns_404_when_the_superlink_does_not_know_the_run(client, src_root, mock_flwr_run):
    # `flwr log` (1.36) handles gRPC NOT_FOUND by logging "Invalid run_id" to stderr and
    # exiting ZERO with empty stdout. A 200 with an empty log would make the hub tell the
    # reader to run the very command that just printed nothing.
    mock_flwr_run(returncode=0, stdout="", stderr="ERROR: Invalid run_id `1`, exiting")

    response = client.get("/run_logs/1")

    assert response.status_code == 404
    assert "Invalid run_id" in response.json()["detail"]


def test_run_logs_returns_502_when_the_superlink_returns_nothing(client, src_root, mock_flwr_run):
    # gRPC DEADLINE_EXCEEDED (SuperLink busy) is swallowed by `flwr log` with `pass`: exit
    # zero, empty stdout, empty stderr. Nothing was retrieved, so say so.
    mock_flwr_run(returncode=0, stdout="", stderr="")

    response = client.get("/run_logs/1")

    assert response.status_code == 502


def test_run_logs_runs_flwr_under_a_timeout(client, src_root, mock_flwr_run):
    # A wedged `flwr log` must not hold a worker forever: the subprocess call carries the
    # run-log timeout (the list/stop commands deliberately run without one).
    commands = mock_flwr_run(stdout=_SERVERAPP_FAILURE)

    client.get("/run_logs/1")

    assert commands.kwargs[0]["timeout"] == 60


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


def test_run_flwr_command_tolerates_non_utf8_output(tmp_path):
    # Researcher code can print anything; one undecodable byte must not turn a retrievable
    # log into a 500. Exercised against a real subprocess, not the mock.
    from fl_api.app import _run_flwr_command

    result = _run_flwr_command(
        ["python3", "-c", "import sys; sys.stdout.buffer.write(b'before \\xff after\\n')"],
        tmp_path,
        "log",
        timeout=30,
    )

    assert result.returncode == 0
    assert result.stdout == "before \ufffd after\n"
