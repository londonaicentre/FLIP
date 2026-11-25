#!/usr/bin/env python3
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
"""Check a running Compose trust node (FLIP#1449): `make -C trust check-node KIT=<CODE>`.

The Compose form of `make -C trust/deploy/helm selftest`: the same checks (node_checks.py), asked
through `docker` instead of `kubectl`, and the same report. It reads the stack that is running and
starts nothing. `selftest_node.sh` runs it against its throwaway stack; `up-onprem-trust` and
`upgrade-onprem-trust` run it against the site's own.

With --site, on a kit that loads no sample data (TRUST_DATA_SEED=false), no study is sent: the node
holds the site's own data, so the C-STORE and the conversion are skipped, and say so.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import node_checks  # noqa: E402
import trust_checks  # noqa: E402
import trust_kit_lib  # noqa: E402
from node_checks import CURL_AUTH, ORTHANC_CURL, ORTHANC_URL  # noqa: E402
from trust_checks import Check, failed, passed  # noqa: E402

RECEIVED_LOG = "/data/xnat/home/logs/received.log"
DICOM_LOG = "/data/xnat/home/logs/dicom.log"
# What XNAT's importer logs when a store arrives and cannot be read (the Helm smoke looks for the same).
IMPORTER_ERRORS = "AbstractMethodError|NoSuchMethodError|unable to read DICOM object null"


class DockerNode:
    """A trust's Compose project and its XNAT swarm stack, reached through docker."""

    def __init__(self, project: str, xnat_stack: str) -> None:
        self.project = project
        self.xnat_stack = xnat_stack

    def _run(self, args: list[str], stdin: str | None = None, timeout: int = 120) -> tuple[bool, str]:
        try:
            result = subprocess.run(args, input=stdin, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, str(e)
        return result.returncode == 0, (result.stdout if result.returncode == 0 else result.stderr).strip()

    def _inspect(self, pod: str) -> dict:
        ok, out = self._run(["docker", "inspect", pod])
        try:
            return json.loads(out)[0] if ok else {}
        except (json.JSONDecodeError, IndexError):
            return {}

    def _names(self, *filters: str, everything: bool = False) -> list[str]:
        args = ["docker", "ps", *(["-a"] if everything else []), "--format", "{{.Names}}"]
        for label in filters:
            args += ["--filter", label]
        ok, out = self._run(args)
        return out.split() if ok else []

    def pod(self, component: str) -> str | None:
        """The running container of a service: XNAT's are swarm tasks, the rest belong to the Compose project."""
        if component.startswith("xnat-"):
            found = self._names(f"label=com.docker.swarm.service.name={self.xnat_stack}_{component}")
        else:
            # One name on both platforms: the Compose service is fl-client-net-1.
            service = "fl-client-net-1" if component == "fl-client" else component
            found = self._names(
                f"label=com.docker.compose.project={self.project}", f"label=com.docker.compose.service={service}"
            )
        return found[0] if found else None

    def container_port(self, pod: str) -> int | None:
        ports = self._inspect(pod).get("Config", {}).get("ExposedPorts") or {}
        return int(next(iter(ports)).split("/")[0]) if ports else None

    def container_state(self, pod: str) -> tuple[bool, int]:
        state = self._inspect(pod)
        if not state:
            return False, -1
        running = state["State"].get("Running", False) and not state["State"].get("Restarting", False)
        return running, int(state.get("RestartCount", 0))

    def mounts(self, pod: str, container: str) -> list[dict]:
        """Each mount as the checks read a Kubernetes one: where it lands, and the kit folder it comes from."""
        return [
            {"mountPath": m.get("Destination"), "subPath": Path(m.get("Source", "")).name}
            for m in self._inspect(pod).get("Mounts", [])
        ]

    def orthanc_credentials(self) -> str | None:
        """user:password of the first Orthanc user, from the running container's environment."""
        pod = self.pod("orthanc")
        for entry in self._inspect(pod).get("Config", {}).get("Env", []) if pod else []:
            name, _, value = entry.partition("=")
            if name == "ORTHANC__REGISTERED_USERS":
                try:
                    return next((f"{user}:{password}" for user, password in json.loads(value).items()), None)
                except (json.JSONDecodeError, AttributeError):
                    return None
        return None

    def exec(self, pod: str, script: str, stdin: str | None = None) -> tuple[bool, str]:
        return self._run(
            ["docker", "exec", *(["-i"] if stdin is not None else []), pod, "sh", "-c", script], stdin=stdin
        )

    def not_running(self) -> list[str] | None:
        """Containers of the project that are neither running nor a one-shot that finished; None when it has none."""
        ok, out = self._run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"label=com.docker.compose.project={self.project}",
                "--format",
                "{{.Names}}\t{{.State}}\t{{.Status}}",
            ]
        )
        rows = [line.split("\t") for line in out.splitlines()] if ok else []
        if not rows:
            return None
        return [f"{name} {status}" for name, state, status in rows if state != "running" and "Exited (0)" not in status]


def check_ready(node: DockerNode) -> Check:
    """Every container of the Compose project is running, and XNAT's web task is too."""
    stopped = node.not_running()
    if stopped is None:
        return failed("services ready", f"Compose project {node.project} has no containers")
    if not node.pod("xnat-web"):
        stopped.append(f"{node.xnat_stack}_xnat-web has no running task")
    if stopped:
        return failed("services ready", "not running: " + ", ".join(stopped))
    return passed("services ready", f"{node.project} and {node.xnat_stack} are running")


def cstore(
    node: DockerNode, settle_seconds: int = 30, sleep: Callable[[float], None] = time.sleep
) -> tuple[int, str, str]:
    """A real C-STORE of one instance from Orthanc to XNAT: (exit code, verdict, "").

    A C-ECHO would pass while every store aborts. Receipt is read from received.log, which XNAT's receiver writes
    one line per object it accepts; dicom.log is XNAT's error log, so it is scanned for importer failures and its
    growth never counts as receipt.
    """
    pod, creds = node.pod("xnat-web"), node.orthanc_credentials()
    if not pod:
        return 1, "no running xnat-web container", ""
    if not creds:
        return 1, "could not read an Orthanc user from its container", ""

    def orthanc(args: str) -> tuple[bool, str]:
        # The credential travels on stdin, never in argv, where the container's process list would show it.
        return node.exec(pod, f"{CURL_AUTH}\nread -r ocreds\n{ORTHANC_CURL} {args}", stdin=creds)

    def lines(log: str) -> int:
        # Only digits survive: the count comes from inside a container.
        _, out = node.exec(pod, f"wc -l < {log} 2>/dev/null || echo 0")
        return int("".join(c for c in out if c.isdigit()) or 0)

    ok, out = orthanc(f"'{ORTHANC_URL}/instances?since=0&limit=1'")
    try:
        instance = json.loads(out)[0] if ok else ""
    except (json.JSONDecodeError, IndexError, TypeError):
        instance = ""
    if not instance:
        return 1, "Orthanc has no instance to send (did seeding run?)", ""

    before, dicom_before = lines(RECEIVED_LOG), lines(DICOM_LOG)
    body = json.dumps({"Resources": [instance], "Synchronous": True})
    ok, store = orthanc(f"-X POST --data-binary '{body}' {ORTHANC_URL}/modalities/XNAT/store")
    try:
        stored = ok and json.loads(store).get("FailedInstancesCount") == 0
    except (json.JSONDecodeError, AttributeError):
        stored = False
    if not stored:
        return 1, f"Orthanc's store to XNAT did not succeed: {store[:160]}", ""
    # XNAT records the receipt after the store has returned, so poll for it rather than read once.
    after = lines(RECEIVED_LOG)
    for _ in range(settle_seconds):
        if after != before:
            break
        sleep(1)
        after = lines(RECEIVED_LOG)
    _, errors = node.exec(pod, f"tail -n +{dicom_before + 1} {DICOM_LOG} 2>/dev/null | grep -cE '{IMPORTER_ERRORS}'")
    if errors.strip().isdigit() and int(errors) > 0:
        return 1, f"XNAT's importer logged {errors.strip()} failure(s) in dicom.log", ""
    # XNAT starts a new received.log with the day's first object, so a count that fell is a receipt too.
    if after == before:
        return 1, f"XNAT never recorded the object: received.log stayed at {before} lines", ""
    return 0, f"1 instance received (received.log {before}->{after}); no importer error in dicom.log", ""


def read_kit(path: Path) -> dict[str, str]:
    kit = {}
    for line in path.read_text().splitlines() if path.is_file() else []:
        key, sep, value = line.strip().partition("=")
        if sep and not key.startswith("#"):
            kit[key.strip()] = value
    return kit


def earlier_rows(path: Path | None) -> list[Check]:
    """Rows a caller recorded before the checks ran, one `name<TAB>true|false<TAB>detail` per line."""
    rows = []
    for line in path.read_text().splitlines() if path else []:
        name, ok, detail = (line.split("\t", 2) + ["", ""])[:3]
        rows.append(trust_checks.verdict(name, ok == "true", detail))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", required=True, help="the trust's Compose project, e.g. trust1")
    parser.add_argument("--xnat-stack", required=True, help="the trust's XNAT swarm stack, e.g. xnat1")
    parser.add_argument("--kit-file", type=Path, required=True)
    parser.add_argument("--backend", choices=("nvflare", "flower"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--stem", help="report file name without its extension (default node-<project>-<stamp>)")
    parser.add_argument("--latest", help="name of the copy that is overwritten each run (default latest-<project>)")
    parser.add_argument("--title", default="Trust node check")
    parser.add_argument(
        "--site",
        action="store_true",
        help="this is the site's own stack: it should reach its hub, and no study is sent unless the kit "
        "loads the sample data",
    )
    parser.add_argument("--earlier-rows", type=Path, help="rows recorded before these checks, to head the report")
    parser.add_argument("--fl-settle-seconds", type=int, default=45)
    parser.add_argument("--conversion-timeout", type=int, default=600)
    args = parser.parse_args(argv)

    kit = read_kit(args.kit_file)
    backend = args.backend
    seeded = trust_kit_lib.trust_data_seed(kit)
    no_data = "" if seeded else "TRUST_DATA_SEED=false, so the node holds no sample data"
    node = DockerNode(args.project, args.xnat_stack)
    started = datetime.now(timezone.utc)
    print(f"== Trust node check ({backend}) of {args.project}, started {started:%Y-%m-%dT%H:%M:%SZ}")
    checks = earlier_rows(args.earlier_rows) + node_checks.run_checks(
        node,
        backend=backend,
        fl_enabled=True,
        ready=lambda: check_ready(node),
        smoke=lambda: cstore(node),
        no_omop=no_data,
        no_orthanc=no_data,
        no_study="TRUST_DATA_SEED=false: the node holds the site's own data, so no study is sent"
        if args.site and not seeded
        else "",
        no_hub="" if args.site else "a self-test stack is joined to no hub",
        conversion_timeout=args.conversion_timeout,
        fl_settle_seconds=args.fl_settle_seconds,
    )
    path = trust_checks.write_report(
        checks,
        args.out,
        stem=args.stem or f"node-{args.project}-{started:%Y%m%dT%H%M%SZ}",
        latest=args.latest or f"latest-{args.project}",
        title=args.title,
        started=f"{started:%Y-%m-%dT%H:%M:%SZ}",
        finished=f"{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ}",
        shape="compose",
        backend=backend,
    )
    print(f"Report: {path}")
    return trust_checks.exit_code(checks)


if __name__ == "__main__":
    sys.exit(main())
