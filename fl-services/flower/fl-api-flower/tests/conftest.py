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

import ipaddress
import subprocess

import pytest
from fastapi.testclient import TestClient

from fl_api import app as app_module
from fl_api.utils import validation


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
    def _mock(*, returncode=0, stdout="", stderr="", exception=None, by_command=None):
        if exception is not None:

            def _raise(*_args, **_kwargs):
                raise exception

            monkeypatch.setattr(app_module.subprocess, "run", _raise)
            return

        if by_command is not None:
            # by_command: {flwr_subcommand: {"returncode": int, "stdout": str, "stderr": str}}
            # e.g. {"stop": {...}, "list": {...}}. command is ["flwr", "<subcommand>", ...].
            def _dispatch(command, *_args, **_kwargs):
                subcommand = command[1] if len(command) > 1 else ""
                spec = by_command.get(subcommand, {})
                return subprocess.CompletedProcess(
                    args=command,
                    returncode=spec.get("returncode", 0),
                    stdout=spec.get("stdout", ""),
                    stderr=spec.get("stderr", ""),
                )

            monkeypatch.setattr(app_module.subprocess, "run", _dispatch)
            return

        monkeypatch.setattr(
            app_module.subprocess,
            "run",
            lambda *_args, **_kwargs: subprocess.CompletedProcess(
                args=[],
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
            ),
        )

    return _mock


# A public address for the stub resolver below: what an S3 endpoint answers. Deliberately not a TEST-NET
# address — those sit in the IANA special-purpose registry and ``ipaddress`` flags them private, which is
# exactly the rejection the stub must never trigger.
STUB_PUBLIC_ADDRESS = ipaddress.ip_address("52.95.150.1")


@pytest.fixture(autouse=True)
def stub_public_resolver(monkeypatch):
    """Resolve every DNS name to one public address, so no test performs a real lookup (FLIP#905).

    ``validate_bundle_url`` resolves DNS names through ``validation.resolve_bundle_host`` and fails closed on
    a resolver error, so the non-resolving ``test.local`` / ``example.com`` bundle URLs used across the suite
    would otherwise be rejected by name resolution — or, worse, depend on the runner's DNS. Tests that need
    a specific answer patch the same seam again. The once-per-process empty-allow-list warning flag is reset
    alongside so the tests of that warning observe its first emission.

    Returns:
        Callable: The real ``resolve_bundle_host`` this fixture displaced, for the one test that exercises it
        against a patched ``socket.getaddrinfo``.
    """
    real_resolve_bundle_host = validation.resolve_bundle_host
    monkeypatch.setattr(validation, "resolve_bundle_host", lambda hostname: [STUB_PUBLIC_ADDRESS])
    monkeypatch.setattr(validation, "_warned_empty_allow_list", False)
    return real_resolve_bundle_host
