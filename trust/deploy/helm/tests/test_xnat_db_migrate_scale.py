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

"""The xnat-db dump hook's scaler, against a stand-in API server (FLIP#2071).

The property under test is the one the upgrade's safety rests on: xnat-web is stopped only for the
dump, and **every** exit that stopped it either leaves a verified dump behind or puts xnat-web back
at the replica count it had. A real cluster cannot be asked for the failure paths cheaply, so the
Kubernetes side is a small HTTP server here: it records the scale calls, answers the pod list, and
can be made to refuse a call on demand.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

CHART_DIR = Path(__file__).resolve().parents[1]
MODULE = CHART_DIR / "config" / "xnat_db_migrate_scale.py"

_spec = importlib.util.spec_from_file_location("xnat_db_migrate_scale", MODULE)
assert _spec is not None
assert _spec.loader is not None
scale = importlib.util.module_from_spec(_spec)
sys.modules["xnat_db_migrate_scale"] = scale
_spec.loader.exec_module(scale)


class _FakeApiServer:
    """The three calls the scaler makes, and nothing else.

    ``pods`` is the queue of answers the pod list gives, one per call, so a test can make xnat-web
    take a few polls to disappear. ``refuse`` makes every write fail with 403, standing in for a
    Role that was not applied.
    """

    def __init__(self, *, replicas: int | None = 1, pods: list[list[str]] | None = None, refuse: bool = False) -> None:
        self.replicas = replicas
        self.pods = pods if pods is not None else [[]]
        self.refuse = refuse
        self.scales: list[int] = []
        self.pod_calls = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # noqa: A002 - the base signature; silences stderr
                pass

            def _send(self, code: int, body: dict) -> None:
                payload = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's interface
                assert self.headers["Authorization"] == "Bearer test-token"  # pragma: allowlist secret
                if "/pods" in self.path:
                    answer = outer.pods[min(outer.pod_calls, len(outer.pods) - 1)]
                    outer.pod_calls += 1
                    self._send(200, {"items": [{"metadata": {"name": name}} for name in answer]})
                else:
                    self._send(200, {"spec": {"replicas": outer.replicas}})

            def do_PATCH(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
                if outer.refuse:
                    self._send(403, {"message": "deployments is forbidden"})
                    return
                outer.scales.append(body["spec"]["replicas"])
                self._send(200, {})

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> _FakeApiServer:
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_port}"


@pytest.fixture
def plan_dir(tmp_path: Path) -> Path:
    (tmp_path / "plan").write_text("dump\n")
    return tmp_path


def _args(plan_dir: Path, url: str, timeout: float = 5.0) -> argparse.Namespace:
    return scale.parse_args(
        [
            "--namespace",
            "flip-trust",
            "--deployment",
            "flip-trust-xnat-web",
            "--selector",
            "app.kubernetes.io/component=xnat-web",
            "--plan-dir",
            str(plan_dir),
            "--timeout-seconds",
            str(timeout),
            "--api-server",
            url,
        ]
    )


def _api(url: str):
    return scale.KubeApi(url, "test-token", None)  # pragma: allowlist secret


def _answer(plan_dir: Path, result: str, delay: float = 0.0) -> threading.Thread:
    """Stand in for the dump container: wait for `go`, then report `result`."""

    def respond() -> None:
        deadline = time.monotonic() + 10
        while not (plan_dir / "go").exists():
            if (plan_dir / "abort").exists() or time.monotonic() > deadline:
                return
            time.sleep(0.02)
        time.sleep(delay)
        (plan_dir / "dump-result").write_text(result)

    thread = threading.Thread(target=respond, daemon=True)
    thread.start()
    return thread


def test_a_skip_plan_never_touches_xnat_web(tmp_path: Path):
    (tmp_path / "plan").write_text("skip\n")
    with _FakeApiServer() as server:
        assert scale.run(_api(server.url), _args(tmp_path, server.url)) == 0
        assert server.scales == [], "a release that does not change the major must leave XNAT alone"


def test_xnat_web_is_stopped_before_the_dump_and_left_down_on_success(plan_dir: Path):
    """On success the upgrade itself brings xnat-web back, onto the restored database."""
    with _FakeApiServer(pods=[["xnat-web-1"], ["xnat-web-1"], []]) as server:
        _answer(plan_dir, "ok")
        assert scale.run(_api(server.url), _args(plan_dir, server.url)) == 0
        assert server.scales == [0]
        assert (plan_dir / "go").exists()
        assert server.pod_calls >= 3, "the wait must keep polling while a terminating pod is listed"


def test_a_failed_dump_puts_xnat_web_back_before_the_job_fails(plan_dir: Path):
    with _FakeApiServer(replicas=2, pods=[[]]) as server:
        _answer(plan_dir, "failed")
        assert scale.run(_api(server.url), _args(plan_dir, server.url)) == 1
        assert server.scales == [0, 2], "xnat-web must come back at the replica count it had"


def test_a_dump_that_never_reports_times_out_and_restores(plan_dir: Path):
    with _FakeApiServer(pods=[[]]) as server:
        assert scale.run(_api(server.url), _args(plan_dir, server.url, timeout=1.0)) == 1
        assert server.scales == [0, 1]


def test_xnat_web_pods_that_never_go_time_out_and_restore(plan_dir: Path):
    with _FakeApiServer(pods=[["xnat-web-1"]]) as server:
        assert scale.run(_api(server.url), _args(plan_dir, server.url, timeout=1.0)) == 1
        assert server.scales == [0, 1]
        assert (plan_dir / "abort").exists(), "the dump container must be told to stop waiting"
        assert not (plan_dir / "go").exists()


def test_a_refused_scale_aborts_without_claiming_xnat_web_is_down(plan_dir: Path):
    with _FakeApiServer(refuse=True) as server:
        assert scale.run(_api(server.url), _args(plan_dir, server.url)) == 1
        assert server.scales == []
        assert (plan_dir / "abort").exists()
        assert not (plan_dir / "go").exists()


def test_an_unreadable_deployment_stops_before_anything_is_changed(plan_dir: Path):
    api = _api("http://127.0.0.1:1")  # nothing listens here
    assert scale.run(api, _args(plan_dir, "http://127.0.0.1:1", timeout=1.0)) == 1
    assert (plan_dir / "abort").exists()


def test_an_absent_replica_count_reads_as_one(plan_dir: Path):
    """Kubernetes defaults spec.replicas to 1; restoring 0 would leave XNAT down."""
    with _FakeApiServer(pods=[[]]) as server:
        server.replicas = None
        _answer(plan_dir, "failed")
        assert scale.run(_api(server.url), _args(plan_dir, server.url)) == 1
        assert server.scales == [0, 1]
