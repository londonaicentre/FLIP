#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
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
"""Unit tests for trust/deploy/scripts/check_node.py, the Compose form of the node checks (FLIP#1449).

docker is faked at DockerNode._run, the one place the script reaches it. The checks themselves are
node_checks.py's and are tested against a fake cluster in trust/deploy/helm/tests/test_selftest_k8s.py;
here: how a Compose node answers them, the C-STORE, and that both platforms report the same rows.

Usage:
    uv run --no-config trust/deploy/scripts/tests/test_check_node.py
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import check_node  # noqa: E402
import node_checks  # noqa: E402
import trust_checks  # noqa: E402

PASS = 0
FAIL = 0


def _assert(condition: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        print(f"  ✅ {label}")
        PASS += 1
    else:
        print(f"  ❌ {label}")
        if detail:
            print(f"    {detail}")
        FAIL += 1


INSPECT = {
    "Config": {
        "ExposedPorts": {"8000/tcp": {}},
        "Env": ["PATH=/bin", 'ORTHANC__REGISTERED_USERS={"orthanc": "s3cret"}'],  # pragma: allowlist secret
    },
    "State": {"Running": True, "Restarting": False},
    "RestartCount": 2,
    "Mounts": [
        {"Source": "/opt/flip/fl-kit/net-1/services/Trust_1/startup", "Destination": "/app/startup"},
        {"Source": "/opt/flip/data/trust-1/images/net-1", "Destination": "/app/data/images/net-1"},
    ],
}


class FakeDocker(check_node.DockerNode):
    """A DockerNode whose docker answers from canned replies, keyed by the first words of the command."""

    def __init__(self, replies: dict[str, tuple[bool, str]] | None = None) -> None:
        super().__init__("trust1", "xnat1")
        self.replies = {"docker inspect": (True, json.dumps([INSPECT])), **(replies or {})}
        self.calls: list[tuple[list[str], str | None]] = []

    def _run(self, args, stdin=None, timeout=120):
        self.calls.append((args, stdin))
        line = " ".join(args)
        for prefix, reply in self.replies.items():
            if prefix in line:
                return reply
        return True, ""


def test_a_container_is_found_by_its_compose_or_swarm_label() -> None:
    node = FakeDocker({"docker ps": (True, "trust1-trust-api-1")})
    _assert(node.pod("trust-api") == "trust1-trust-api-1", "a Compose service by project and service label")
    filters = [a for a in node.calls[-1][0] if a.startswith("label=")]
    _assert(
        filters == ["label=com.docker.compose.project=trust1", "label=com.docker.compose.service=trust-api"],
        "both labels are asked for",
        str(filters),
    )
    node.pod("fl-client")
    _assert("label=com.docker.compose.service=fl-client-net-1" in node.calls[-1][0], "fl-client is fl-client-net-1")
    node.pod("xnat-web")
    _assert(
        "label=com.docker.swarm.service.name=xnat1_xnat-web" in node.calls[-1][0], "XNAT's by its swarm service name"
    )
    _assert(FakeDocker({"docker ps": (True, "")}).pod("orthanc") is None, "no running container is None")


def test_a_container_answers_what_the_shared_checks_ask() -> None:
    node = FakeDocker({"docker ps": (True, "trust1-orthanc-1")})
    _assert(node.container_port("x") == 8000, "its port is the image's exposed port")
    _assert(node.container_state("x") == (True, 2), "its state is running and its restart count")
    restarting = {**INSPECT, "State": {"Running": True, "Restarting": True}}
    looping = FakeDocker({"docker inspect": (True, json.dumps([restarting]))})
    _assert(looping.container_state("x") == (False, 2), "a restarting container is not running")
    _assert(FakeDocker({"docker inspect": (False, "No such object")}).container_state("x") == (False, -1), "gone")
    _assert(
        node_checks.nvflare_startup_dir(node, "x") == "/app/startup",
        "the kit's startup/ folder is found where it is mounted",
    )
    _assert(node.orthanc_credentials() == "orthanc:s3cret", "Orthanc's user comes from its container's environment")
    node.exec("x", "true", stdin="secret")
    _assert(node.calls[-1] == (["docker", "exec", "-i", "x", "sh", "-c", "true"], "secret"), "stdin goes to exec -i")


def test_services_ready_names_what_is_not_running() -> None:
    up = (
        "trust1-trust-api-1\trunning\tUp 2 hours\ttrust-api\n"
        "trust1-fl-client-init-1\texited\tExited (0) 2 hours ago\tfl-client-init\n"
    )
    ready = check_node.check_ready(FakeDocker({"docker ps -a": (True, up), "docker ps": (True, "xnat1_xnat-web.1")}))
    _assert(ready.ok and "trust1 and xnat1" in ready.detail, "running services and finished one-shots are ready")
    down = up + "trust1-orthanc-1\texited\tExited (1) 5 seconds ago\torthanc\n"
    broken = check_node.check_ready(FakeDocker({"docker ps -a": (True, down), "docker ps": (True, "x")}))
    _assert(
        not broken.ok and "trust1-orthanc-1 Exited (1)" in broken.detail, "a stopped service is named", broken.detail
    )
    no_xnat = check_node.check_ready(FakeDocker({"docker ps -a": (True, up), "docker ps": (True, "")}))
    _assert(not no_xnat.ok and "xnat1_xnat-web" in no_xnat.detail, "a missing XNAT task is named", no_xnat.detail)
    empty = check_node.check_ready(FakeDocker({"docker ps": (True, "")}))
    _assert(not empty.ok and "has no containers" in empty.detail, "a project with no containers is not ready")
    stopped = up + "trust1-omop-db-1\texited\tExited (0) 1 minute ago\tomop-db\n"
    clean_stop = check_node.check_ready(FakeDocker({"docker ps -a": (True, stopped), "docker ps": (True, "x")}))
    _assert(
        not clean_stop.ok and "trust1-omop-db-1" in clean_stop.detail,
        "a service that stopped cleanly is not a finished one-shot",
        clean_stop.detail,
    )
    refused = trust_checks.run(
        "services ready",
        lambda: check_node.check_ready(
            FakeDocker({"docker ps -a": (False, "permission denied"), "docker ps": (True, "x")})
        ),
    )
    _assert(refused.status is trust_checks.Status.ERROR, "a docker that will not list could not run", refused.detail)


def test_a_docker_that_cannot_run_could_not_run() -> None:
    node = check_node.DockerNode("trust1", "xnat1")
    with mock.patch.object(check_node.subprocess, "run", side_effect=FileNotFoundError("docker")):
        check = trust_checks.run("services ready", lambda: check_node.check_ready(node))
    _assert(check.status is trust_checks.Status.ERROR, "a missing docker is could not run, not a failed node")
    _assert("`docker ps` did not run" in check.detail, "and says what did not run", check.detail)


def test_a_missing_kit_file_checks_nothing() -> None:
    """An empty kit would read as "sample data on" and send a study into a site's own XNAT."""
    with tempfile.TemporaryDirectory() as tmp, mock.patch.object(check_node, "DockerNode") as node:
        argv = ["--site", "--project", "trust1", "--xnat-stack", "xnat1", "--backend", "nvflare"]
        code = check_node.main([*argv, "--kit-file", f"{tmp}/.env.MISSING.prod", "--out", f"{tmp}/out"])
        _assert(code == 2 and not node.called, "exits 2 before touching docker")


def test_a_sites_own_stack_without_sample_data_is_sent_no_study() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        kit = Path(tmp) / ".env.SITE.prod"
        kit.write_text("TRUST_DATA_SEED=false\n")
        fake = StoreNode(["10", "11"])
        with (
            mock.patch.object(check_node, "DockerNode", return_value=fake),
            mock.patch.object(node_checks.time, "sleep"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            argv = ["--site", "--project", "trust1", "--xnat-stack", "xnat1", "--backend", "nvflare"]
            check_node.main([*argv, "--kit-file", str(kit), "--out", f"{tmp}/out", "--fl-settle-seconds", "0"])
        report = json.loads(next((Path(tmp) / "out").glob("node-trust1-*.json")).read_text())
    rows = {row["name"]: row for row in report["checks"]}
    for name in ("xnat c-store", "dcm2niix conversion"):
        _assert(rows[name]["status"] == "skipped" and "site's own data" in rows[name]["detail"], f"{name} is skipped")
    _assert(not any("/modalities/XNAT/store" in c[0][1] for c in fake.calls if c[0][0] == "exec"), "no store is sent")
    _assert(rows["hub heartbeat"]["status"] != "skipped", "and the hub heartbeat is checked")
    _assert(list(rows) == list(node_checks.ROWS), "main() reports every row, by name and in order", str(list(rows)))


class StoreNode(FakeDocker):
    """XNAT and Orthanc as the C-STORE sees them: the received.log count it reads, read by read."""

    def __init__(self, received: list[str], instances='["abc"]', store='{"FailedInstancesCount": 0}', errors="0"):
        super().__init__({"docker ps": (True, "xnat1_xnat-web.1")})
        self.received, self.instances, self.store, self.errors = list(received), instances, store, errors

    def exec(self, pod, script, stdin=None):
        self.calls.append((["exec", script], stdin))
        if "/instances?" in script:
            return True, self.instances
        if "/modalities/XNAT/store" in script:
            return True, self.store
        if "grep -cE" in script:
            return True, self.errors
        if "wc -l" in script and "received.log" in script:
            return True, self.received.pop(0) if len(self.received) > 1 else self.received[0]
        return True, "7"


def test_cstore_reads_the_receipt_from_received_log() -> None:
    waits: list[float] = []
    code, said, _ = check_node.cstore(StoreNode(["10", "10", "10", "11"]), sleep=waits.append)
    _assert(code == 0 and "10->11" in said and len(waits) == 2, "a receipt that lands late still passes", said)
    code, said, _ = check_node.cstore(StoreNode(["3854", "1"]), sleep=waits.append)
    _assert(code == 0, "a count that fell is a receipt: XNAT started the day's new received.log", said)
    code, said, _ = check_node.cstore(StoreNode(["10"]), settle_seconds=3, sleep=waits.append)
    _assert(code == 1 and "received.log stayed at 10" in said, "a store XNAT never recorded fails", said)

    class Deaf(StoreNode):
        def exec(self, pod, script, stdin=None):
            return (False, "Error response from daemon") if "wc -l" in script else super().exec(pod, script, stdin)

    code, said, _ = check_node.cstore(Deaf(["10"]), sleep=waits.append)
    _assert(code == 1 and "could not read XNAT's logs" in said, "logs that cannot be read are not a receipt", said)
    code, said, _ = check_node.cstore(StoreNode(["10", "11"], errors=""), sleep=waits.append)
    _assert(
        code == 1 and "could not read XNAT's logs" in said, "nor is an importer-error count that did not come", said
    )
    code, said, _ = check_node.cstore(StoreNode(["10", "11"], errors="2"), sleep=waits.append)
    _assert(code == 1 and "2 failure(s) in dicom.log" in said, "an importer error fails it, receipt or not", said)
    node = StoreNode(["10", "11"], store='{"FailedInstancesCount": 1}')
    code, said, _ = check_node.cstore(node, sleep=waits.append)
    _assert(code == 1 and "did not succeed" in said, "a store Orthanc reports as failed fails", said)
    node = StoreNode(["10"], instances="[]")
    code, said, _ = check_node.cstore(node, sleep=waits.append)
    _assert(code == 1 and "no instance to send" in said, "an empty Orthanc fails", said)
    _assert(not any("/modalities/XNAT/store" in c[0][1] for c in node.calls), "and no store is attempted")


def test_cstore_keeps_the_credential_out_of_argv_and_runs_nothing_a_container_prints() -> None:
    node = StoreNode(["10", "11"])
    check_node.cstore(node, sleep=lambda _: None)
    orthanc_calls = [c for c in node.calls if "/instances?" in c[0][1] or "/store" in c[0][1]]
    _assert(all(stdin == "orthanc:s3cret" for _, stdin in orthanc_calls), "the Orthanc credential goes on stdin")
    _assert(not any("s3cret" in c[0][1] for c in node.calls), "and never into a command line")
    with tempfile.TemporaryDirectory() as tmp:
        canary = Path(tmp) / "PWNED"
        code, said, _ = check_node.cstore(StoreNode([f"a[$(touch {canary})]"]), settle_seconds=1, sleep=lambda _: None)
        _assert(not canary.exists() and code == 1, "a crafted line count is only ever read for its digits", said)


def test_earlier_rows_keep_their_verdict() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        rows = Path(tmp) / "rows"
        rows.write_text("kit file\ttrue\t/tmp/kit\nstack up\tfalse\tskipped: a preparation step above failed\n")
        first, second = check_node.earlier_rows(rows)
    _assert(first.ok and first.detail == "/tmp/kit", "a passed row")
    _assert(second.status is trust_checks.Status.FAIL, "a failed row stays failed whatever its detail says")
    _assert(check_node.earlier_rows(None) == [], "no file, no rows")


class DriftNode(FakeDocker):
    """A project whose containers are given as {name: (service, config hash, image name, image id)}."""

    def __init__(self, containers: dict[str, tuple[str, str, str, str]], host_images: dict[str, str]) -> None:
        super().__init__()
        self.containers, self.host_images = containers, host_images

    def _run(self, args, stdin=None, timeout=120):
        self.calls.append((args, stdin))
        if args[:2] == ["docker", "ps"]:
            return True, "\n".join(self.containers)
        if args[:3] == ["docker", "image", "inspect"]:
            return (args[-1] in self.host_images), self.host_images.get(args[-1], "No such image")
        service, config_hash, image, image_id = self.containers[args[-1]]
        labels = {"com.docker.compose.service": service, "com.docker.compose.config-hash": config_hash}
        if service == "oneoff":
            labels["com.docker.compose.oneoff"] = "True"
        return True, json.dumps([{"Config": {"Labels": labels, "Image": image}, "Image": image_id}])


def test_drift_names_each_service_that_no_longer_matches() -> None:
    declared = {"trust-api": "h1", "orthanc": "h2", "omop-db": "h3"}
    images = {"ghcr.io/x/trust-api:v1": "sha256:aaa", "ghcr.io/x/orthanc:v1": "sha256:bbb"}
    clean = {
        "t-trust-api-1": ("trust-api", "h1", "ghcr.io/x/trust-api:v1", "sha256:aaa"),
        "t-orthanc-1": ("orthanc", "h2", "ghcr.io/x/orthanc:v1", "sha256:bbb"),
        "t-omop-db-1": ("omop-db", "h3", "ghcr.io/x/omop-db:v1", "sha256:ccc"),  # its image is gone from the host
        "t-run-9": ("oneoff", "zz", "ghcr.io/x/trust-api:v1", "sha256:aaa"),  # a `compose run` is nobody's drift
    }
    check = check_node.check_drift(DriftNode(clean, images), declared)
    _assert(
        check.status is trust_checks.Status.PASS and "3 services match" in check.detail, "a clean project", check.detail
    )
    _assert("XNAT's stack not compared" in check.detail, "a pass says what it did not compare")
    _assert(check.refs == ("SATRE 5.5.01", "SATRE 2.4.03", "SATRE 2.4.04"), "it cites its requirements")

    drifted = {
        **clean,
        "t-trust-api-1": ("trust-api", "EDITED", "ghcr.io/x/trust-api:v1", "sha256:aaa"),
        "t-orthanc-1": ("orthanc", "h2", "ghcr.io/x/orthanc:v1", "sha256:OLD"),
        "t-extra-1": ("shell", "h9", "busybox", "sha256:ddd"),
    }
    del drifted["t-omop-db-1"]
    said = check_node.check_drift(DriftNode(drifted, images), declared).detail
    for expected in (
        "trust-api: its configuration differs from the compose files and kit",
        "orthanc: runs an older ghcr.io/x/orthanc:v1 than this host now holds",
        "t-extra-1 runs a service the compose files do not declare",
        "omop-db: declared, and has no container",
    ):
        _assert(expected in said, expected, said)


def test_drift_without_a_declared_configuration_could_not_run_and_only_reads() -> None:
    check = trust_checks.run("no configuration drift", lambda: check_node.check_drift(DriftNode({}, {}), {}))
    _assert(check.status is trust_checks.Status.ERROR, "an empty `compose config --hash` is could not run, not a pass")
    node = DriftNode({"t-trust-api-1": ("trust-api", "h1", "img", "sha256:a")}, {"img": "sha256:a"})
    check_node.check_drift(node, {"trust-api": "h1"})
    verbs = {" ".join(args[1:3]) for args, _ in node.calls}
    _assert(
        verbs <= {"ps -a", "inspect t-trust-api-1", "image inspect"},
        "docker is only asked to list and inspect",
        str(verbs),
    )
    with tempfile.TemporaryDirectory() as tmp:
        hashes = Path(tmp) / "hashes"
        hashes.write_text("trust-api abc\northanc def\n\n")
        _assert(check_node.read_declared(hashes) == {"trust-api": "abc", "orthanc": "def"}, "the hashes file is read")
    _assert(check_node.read_declared(None) is None, "no file: the row is skipped, not run")


def _rows(checks) -> list[str]:
    return [c.name for c in checks]


def test_both_platforms_report_the_same_rows() -> None:
    """The contract that keeps the platforms from drifting: each reports node_checks.ROWS, by name and in order."""
    with contextlib.redirect_stdout(io.StringIO()):  # the fake answers nothing, so most rows fail: only names matter
        compose = node_checks.run_checks(
            FakeDocker({"docker ps": (True, "c")}),
            backend="nvflare",
            fl_enabled=True,
            ready=lambda: trust_checks.passed("services ready"),
            smoke=lambda: (0, "ok", ""),
            fl_settle_seconds=0,
            conversion_timeout=0,
        )
    _assert(_rows(compose) == list(node_checks.ROWS), "Compose", str(_rows(compose)))

    # Kubernetes is held to the same list by test_the_release_reports_the_rows_every_platform_reports in
    # trust/deploy/helm/tests/test_selftest_k8s.py, which runs under pytest with the chart's fake cluster.


def main() -> None:
    test_a_container_is_found_by_its_compose_or_swarm_label()
    test_a_container_answers_what_the_shared_checks_ask()
    test_services_ready_names_what_is_not_running()
    test_a_docker_that_cannot_run_could_not_run()
    test_a_missing_kit_file_checks_nothing()
    test_a_sites_own_stack_without_sample_data_is_sent_no_study()
    test_cstore_reads_the_receipt_from_received_log()
    test_cstore_keeps_the_credential_out_of_argv_and_runs_nothing_a_container_prints()
    test_earlier_rows_keep_their_verdict()
    test_drift_names_each_service_that_no_longer_matches()
    test_drift_without_a_declared_configuration_could_not_run_and_only_reads()
    test_both_platforms_report_the_same_rows()
    print("—")
    print(f"PASS={PASS}  FAIL={FAIL}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
