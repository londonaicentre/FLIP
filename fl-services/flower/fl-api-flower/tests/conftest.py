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


@pytest.fixture
def mock_flwr_run(monkeypatch):
    def _mock(*, returncode=0, stdout="", stderr="", exception=None):
        if exception is not None:
            def _raise(*_args, **_kwargs):
                raise exception

            monkeypatch.setattr(app_module.subprocess, "run", _raise)
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
