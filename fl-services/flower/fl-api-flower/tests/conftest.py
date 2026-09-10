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
from fastapi.testclient import TestClient

from fl_api import app as app_module


@pytest.fixture
def client():
    with TestClient(app_module.app) as test_client:
        yield test_client


@pytest.fixture
def src_root(tmp_path, monkeypatch):
    root = tmp_path / "src"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FLOWER_SRC_ROOT", str(root))
    return root


@pytest.fixture(autouse=True)
def reset_submission_state():
    app_module._submission_in_progress = False
    yield
    app_module._submission_in_progress = False


@pytest.fixture(autouse=True)
def reset_node_mapping():
    app_module._node_trust_mapping.clear()
    yield
    app_module._node_trust_mapping.clear()


@pytest.fixture
def mock_flwr_run(monkeypatch):
    # Returns the list of argv lists the app handed to subprocess.run, so a test can
    # assert on the command it built as well as on the response.
    def _mock(*, returncode=0, stdout="", stderr="", exception=None, by_command=None):
        commands: list[list[str]] = []

        if exception is not None:

            def _raise(command=None, *_args, **_kwargs):
                commands.append(command)
                raise exception

            monkeypatch.setattr(app_module.subprocess, "run", _raise)
            return commands

        if by_command is not None:
            # by_command: {flwr_subcommand: {"returncode": int, "stdout": str, "stderr": str}}
            # e.g. {"stop": {...}, "list": {...}}. command is ["uvx", "flwr", "<subcommand>", ...].
            def _dispatch(command, *_args, **_kwargs):
                commands.append(command)
                subcommand = command[2] if len(command) > 2 else ""
                spec = by_command.get(subcommand, {})
                return subprocess.CompletedProcess(
                    args=command,
                    returncode=spec.get("returncode", 0),
                    stdout=spec.get("stdout", ""),
                    stderr=spec.get("stderr", ""),
                )

            monkeypatch.setattr(app_module.subprocess, "run", _dispatch)
            return commands

        def _fixed(command=None, *_args, **_kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(
                args=command or [],
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
            )

        monkeypatch.setattr(app_module.subprocess, "run", _fixed)
        return commands

    return _mock
