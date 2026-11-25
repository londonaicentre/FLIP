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
through `docker` instead of `kubectl`, and the same report. It checks the stack that is running and
starts no service. Unless told otherwise (below) it does send one study from Orthanc to XNAT, where it
stays in the prearchive, and converts one in a throwaway XNAT project it deletes. `selftest_node.sh`
runs it against its throwaway stack; `up-onprem-trust` and `upgrade-onprem-trust` against the site's own.

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
            # docker itself is missing, unreachable or hung: the check could not run, which is not "the node failed".
            raise RuntimeError(f"`{' '.join(args[:2])}` did not run: {e}") from e
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
        """Containers of the project that are neither running nor a finished one-shot; None when it has none.

        A one-shot is a service named `…-init`. Any other service that has stopped is not running, exit 0 or not:
        a database told to stop exits 0 too.
        """
        ok, out = self._run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"label=com.docker.compose.project={self.project}",
                "--format",
                '{{.Names}}\t{{.State}}\t{{.Status}}\t{{.Label "com.docker.compose.service"}}',
            ]
        )
        if not ok:
            raise RuntimeError(f"docker could not list the project's containers: {out[:160]}")
        rows = [line.split("\t") for line in out.splitlines() if line.count("\t") == 3]
        if not rows:
            return None
        return [
            f"{name} {status}"
            for name, state, status, service in rows
            if state != "running" and not (service.endswith("-init") and "Exited (0)" in status)
        ]


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


def check_drift(node: DockerNode, declared: dict[str, str]) -> Check:
    """The Compose project still is what its compose files and kit describe (FLIP#2078).

    ``declared`` is `docker compose config --hash '*'`: each service and the hash of its rendered configuration.
    Compose stamps that hash on every container it creates, so a service whose compose file, kit value or image
    name changed since it was started shows here. The hash covers the image's name and not its content, so each
    container's image is also compared with the image its name now resolves to on this host.

    Not covered: XNAT's swarm stack, which carries no such hash; and a container made by hand with the label copied.
    """
    if not declared:
        raise RuntimeError("no declared configuration: `docker compose config --hash '*'` printed nothing")
    differences = []
    seen: set[str] = set()
    for name in node._names(f"label=com.docker.compose.project={node.project}", everything=True):
        container = node._inspect(name)
        labels = container.get("Config", {}).get("Labels") or {}
        if labels.get("com.docker.compose.oneoff") == "True":
            continue
        service = labels.get("com.docker.compose.service", "")
        seen.add(service)
        if service not in declared:
            differences.append(f"{name} runs a service the compose files do not declare")
            continue
        if labels.get("com.docker.compose.config-hash") != declared[service]:
            differences.append(f"{service}: its configuration differs from the compose files and kit")
        image = container.get("Config", {}).get("Image", "")
        ok, current = node._run(["docker", "image", "inspect", "--format", "{{.Id}}", image])
        if not ok:
            differences.append(f"{service}: its image {image or '(unnamed)'} is no longer on this host to compare with")
        elif current.strip() != container.get("Image"):
            differences.append(f"{service}: runs an older {image} than this host now holds")
    differences += [f"{service}: declared, and has no container" for service in sorted(set(declared) - seen)]
    return node_checks.drift(
        differences, f"{len(declared)} services match their compose configuration and image (XNAT's stack not compared)"
    )


def read_declared(path: Path | None) -> dict[str, str] | None:
    """`docker compose config --hash '*'` as {service: hash}; None when no file was given."""
    if path is None:
        return None
    return dict(line.split()[:2] for line in path.read_text().splitlines() if len(line.split()) >= 2)


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

    def lines(log: str) -> int | None:
        # Only digits survive: the count comes from inside a container. None when the container could not be asked.
        ok, out = node.exec(pod, f"wc -l < {log} 2>/dev/null || echo 0")
        return int("".join(c for c in out if c.isdigit()) or 0) if ok else None

    ok, out = orthanc(f"'{ORTHANC_URL}/instances?since=0&limit=1'")
    try:
        instance = json.loads(out)[0] if ok else ""
    except (json.JSONDecodeError, IndexError, TypeError):
        instance = ""
    if not instance:
        return 1, "Orthanc has no instance to send (did seeding run?)", ""

    before, dicom_before = lines(RECEIVED_LOG), lines(DICOM_LOG)
    if before is None or dicom_before is None:
        return 1, "could not read XNAT's logs in its container before the store", ""
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
        if after is None or after != before:
            break
        sleep(1)
        after = lines(RECEIVED_LOG)
    # grep -c exits 1 on a count of 0, which is an answer here and not a failure.
    ok, errors = node.exec(
        pod, f"tail -n +{dicom_before + 1} {DICOM_LOG} 2>/dev/null | grep -cE '{IMPORTER_ERRORS}' || true"
    )
    if after is None or not ok or not errors.strip().isdigit():
        return 1, "could not read XNAT's logs in its container after the store", ""
    if int(errors) > 0:
        return 1, f"XNAT's importer logged {errors.strip()} failure(s) in dicom.log", ""
    # XNAT starts a new received.log with the day's first object, so a count that fell is a receipt too.
    if after == before:
        return 1, f"XNAT never recorded the object: received.log stayed at {before} lines", ""
    return 0, f"1 instance received (received.log {before}->{after}); no importer error in dicom.log", ""


def read_kit(path: Path) -> dict[str, str]:
    kit = {}
    for line in path.read_text().splitlines():
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
    parser.add_argument(
        "--declared-hashes",
        type=Path,
        help="output of `docker compose config --hash '*'` for this kit: what the drift row compares the node with",
    )
    parser.add_argument("--earlier-rows", type=Path, help="rows recorded before these checks, to head the report")
    parser.add_argument("--fl-settle-seconds", type=int, default=45)
    parser.add_argument("--conversion-timeout", type=int, default=600)
    args = parser.parse_args(argv)

    # An empty kit would read as "sample data on": never send a study on the strength of a kit that is not there.
    no_kit = "" if args.kit_file.is_file() else f"no kit file at {args.kit_file}, so no study is sent"
    if no_kit and args.site:
        print(f"❌ no kit file at {args.kit_file}: nothing was checked", file=sys.stderr)
        return 2
    kit = {} if no_kit else read_kit(args.kit_file)
    backend = args.backend
    seeded = trust_kit_lib.trust_data_seed(kit)
    site_data = args.site and not seeded
    no_data = "" if seeded else "TRUST_DATA_SEED=false, so the node holds no sample data"
    node = DockerNode(args.project, args.xnat_stack)
    declared = read_declared(args.declared_hashes)
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
        no_study=no_kit
        or ("TRUST_DATA_SEED=false: the node holds the site's own data, so no study is sent" if site_data else ""),
        no_hub="" if args.site else "a self-test stack is joined to no hub",
        find_drift=(lambda: check_drift(node, declared)) if declared is not None else None,
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
