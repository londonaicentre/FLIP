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
"""Self-test of a deployed flip-trust release (FLIP#1390): `make -C trust/deploy/helm selftest`.

The Kubernetes form of the node self-test (scripts/selftest_node.sh), the acceptance checklist
every trust shape runs: the release is ready, the FL client has its kit, the three APIs are
healthy, OMOP and Orthanc are seeded, Orthanc refuses anonymous requests, XNAT accepts a real
C-STORE (smoke-cstore.sh), dcm2niix converts a stored series to NIfTI, and the FL client stays
up. It needs no hub: an unreachable hub or FL server is not a failure. It runs from the
operator's machine through kubectl, as `status` and `smoke-cstore` do, and writes the same JSON
and markdown report as the node self-test.

Every check runs in a pod the release already has, so the self-test adds no RBAC and no
NetworkPolicy exception to the trust. The conversion check starts one dcm2niix Job, the same way
a cohort pull does, in a throwaway XNAT project it deletes afterwards.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shlex
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

SCRIPTS_DIR = Path(__file__).resolve().parent
ORTHANC_URL = "http://orthanc:8042"
# XNAT as the xnat-web pod reaches itself.
XNAT_URL = "http://127.0.0.1:8080"
# The throwaway project the conversion check imports into, deleted when the check ends. Its name
# marks it as the check's own, so a later run clears it only if the check created it.
SELFTEST_PROJECT = "FLIPSELFTEST"
SELFTEST_NAME = "FLIP self-test"
# How many Orthanc series the conversion check looks at to pick the smallest one to convert.
SERIES_SAMPLE = 50
API_COMPONENTS = ("trust-api", "imaging-api", "data-access-api")
ANSI = re.compile(r"\x1b\[[0-9;]*m")


@dataclass(frozen=True)
class Check:
    """One line of the report."""

    name: str
    ok: bool
    detail: str


class Cluster(Protocol):
    """What the checks ask of the cluster; KubectlCluster answers for real, the tests fake it."""

    def workloads(self) -> list[dict]: ...
    def pod(self, component: str) -> str | None: ...
    def container_port(self, pod: str) -> int | None: ...
    def orthanc_credentials(self) -> str | None: ...
    def container_state(self, pod: str) -> tuple[bool, int]: ...
    def exec(self, pod: str, script: str, stdin: str | None = None) -> tuple[bool, str]: ...


class KubectlCluster:
    """A release in a namespace, reached through kubectl (and helm for its values)."""

    def __init__(self, namespace: str, release: str, context: str | None) -> None:
        self.namespace = namespace
        self.release = release
        self.kubectl = ["kubectl", *(["--context", context] if context else []), "-n", namespace]
        self.helm = ["helm", *(["--kube-context", context] if context else []), "-n", namespace]

    def _run(self, args: list[str], stdin: str | None = None, timeout: int = 120) -> tuple[bool, str]:
        try:
            result = subprocess.run(args, input=stdin, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, str(e)
        return result.returncode == 0, (result.stdout if result.returncode == 0 else result.stderr).strip()

    def _json(self, args: list[str]) -> dict | None:
        ok, out = self._run(args)
        if not ok:
            return None
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return None

    def values(self) -> dict | None:
        return self._json([*self.helm, "get", "values", self.release, "--all", "-o", "json"])

    def workloads(self) -> list[dict]:
        found = self._json(
            [
                *self.kubectl,
                "get",
                "deploy,statefulset,daemonset",
                "-l",
                f"app.kubernetes.io/instance={self.release}",
                "-o",
                "json",
            ]
        )
        return (found or {}).get("items", [])

    def pod(self, component: str) -> str | None:
        found = self._json(
            [
                *self.kubectl,
                "get",
                "pods",
                "-l",
                f"app.kubernetes.io/instance={self.release},app.kubernetes.io/component={component}",
                "--field-selector=status.phase=Running",
                "-o",
                "json",
            ]
        )
        items = (found or {}).get("items", [])
        return items[0]["metadata"]["name"] if items else None

    def _pod_json(self, pod: str) -> dict:
        return self._json([*self.kubectl, "get", "pod", pod, "-o", "json"]) or {}

    def container_port(self, pod: str) -> int | None:
        ports = self._pod_json(pod).get("spec", {}).get("containers", [{}])[0].get("ports", [])
        return ports[0].get("containerPort") if ports else None

    def container_state(self, pod: str) -> tuple[bool, int]:
        statuses = self._pod_json(pod).get("status", {}).get("containerStatuses", [])
        if not statuses:
            return False, -1
        return "running" in statuses[0].get("state", {}), int(statuses[0].get("restartCount", 0))

    def orthanc_credentials(self) -> str | None:
        """user:password of the first Orthanc user, from the Secret its Deployment reads."""
        deploys = self._json(
            [
                *self.kubectl,
                "get",
                "deploy",
                "-l",
                f"app.kubernetes.io/instance={self.release},app.kubernetes.io/component=orthanc",
                "-o",
                "json",
            ]
        )
        for deploy in (deploys or {}).get("items", []):
            for env in deploy["spec"]["template"]["spec"]["containers"][0].get("env", []):
                ref = env.get("valueFrom", {}).get("secretKeyRef")
                if env.get("name") == "ORTHANC__REGISTERED_USERS" and ref:
                    secret = self._json([*self.kubectl, "get", "secret", ref["name"], "-o", "json"]) or {}
                    raw = secret.get("data", {}).get(ref["key"])
                    if not raw:
                        return None
                    users = json.loads(base64.b64decode(raw))
                    return next((f"{user}:{password}" for user, password in users.items()), None)
        return None

    def exec(self, pod: str, script: str, stdin: str | None = None) -> tuple[bool, str]:
        return self._run(
            [*self.kubectl, "exec", *(["-i"] if stdin is not None else []), pod, "--", "sh", "-c", script], stdin=stdin
        )


# ── the checks ──────────────────────────────────────────────────────────────────────


def check_release_ready(cluster: Cluster) -> Check:
    """Every Deployment, StatefulSet and DaemonSet of the release has all its replicas ready."""
    items = cluster.workloads()
    if not items:
        return Check("release ready", False, "the release has no workloads in this namespace")
    short = []
    for item in items:
        status = item.get("status", {})
        if item["kind"] == "DaemonSet":
            want, ready = status.get("desiredNumberScheduled", 0), status.get("numberReady", 0)
        else:
            want, ready = item.get("spec", {}).get("replicas", 1), status.get("readyReplicas", 0)
        if ready < want:
            short.append(f"{item['metadata']['name']} {ready}/{want}")
    if short:
        return Check("release ready", False, "not ready: " + ", ".join(short))
    return Check("release ready", True, f"{len(items)} workloads ready")


def check_fl_kit(cluster: Cluster, backend: str) -> Check:
    """The FL client sees its participant kit where the image reads it (templates/fl-client.yaml)."""
    pod = cluster.pod("fl-client")
    if not pod:
        return Check("fl kit delivered", False, "no running fl-client pod")
    if backend == "nvflare":
        where, script = "/app/startup/fed_client.json", "test -s /app/startup/fed_client.json"
    else:
        where, script = "/certs/ca.crt and /keys", 'test -s /certs/ca.crt && [ -n "$(ls -A /keys)" ]'
    ok, _ = cluster.exec(pod, script)
    return Check("fl kit delivered", ok, f"{where} in {pod}" if ok else f"{pod} has no kit at {where}")


def check_health(cluster: Cluster, component: str) -> Check:
    """The API answers its own /health with status ok, asked from inside its pod."""
    name = f"{component} health"
    pod = cluster.pod(component)
    if not pod:
        return Check(name, False, f"no running {component} pod")
    port = cluster.container_port(pod) or 8000
    script = (
        'python3 -c "import urllib.request; '
        f"print(urllib.request.urlopen('http://127.0.0.1:{port}/health', timeout=10).read().decode())\""
    )
    ok, body = cluster.exec(pod, script)
    try:
        healthy = ok and json.loads(body).get("status") == "ok"
    except (json.JSONDecodeError, AttributeError):
        healthy = False
    return Check(
        name, healthy, f"ok from :{port}/health" if healthy else f"no healthy answer from :{port}/health ({body[:120]})"
    )


def check_omop_seeded(cluster: Cluster) -> Check:
    """omop-db holds people: the seed hook loaded this trust's slice of the dataset."""
    pod = cluster.pod("omop-db")
    if not pod:
        return Check("omop seeded", False, "no running omop-db pod")
    ok, out = cluster.exec(pod, 'psql -X -tAc "select count(*) from omop.person" -U "$POSTGRES_USER" -d "$POSTGRES_DB"')
    count = out.strip()
    if ok and count.isdigit() and int(count) > 0:
        return Check("omop seeded", True, f"{count} rows in omop.person")
    return Check("omop seeded", False, f"omop.person is empty or unreadable ({count[:120]})")


def check_orthanc(cluster: Cluster) -> tuple[Check, Check]:
    """Orthanc holds studies, and refuses a request without credentials.

    Both asked from the xnat-web pod, which the chart's NetworkPolicies already let reach Orthanc
    (it is the C-STORE receiver smoke-cstore drives from there).
    """
    pod = cluster.pod("xnat-web")
    if not pod:
        missing = "no running xnat-web pod to ask Orthanc from"
        return Check("orthanc seeded", False, missing), Check("orthanc requires auth", False, missing)

    creds = cluster.orthanc_credentials()
    if not creds:
        seeded = Check("orthanc seeded", False, "could not read an Orthanc user from its Secret")
    else:
        # The credential travels on stdin, never in argv, where the pod's process list would show it.
        ok, out = cluster.exec(
            pod, f'read -r creds; exec curl -fsS --max-time 30 -u "$creds" {ORTHANC_URL}/statistics', stdin=creds
        )
        try:
            instances = int(json.loads(out).get("CountInstances", 0)) if ok else 0
        except (json.JSONDecodeError, AttributeError, ValueError):
            instances = 0
        seeded = (
            Check("orthanc seeded", True, f"{instances} instances")
            if instances > 0
            else Check("orthanc seeded", False, f"Orthanc holds no instances ({out[:120]})")
        )

    _, code = cluster.exec(pod, f"curl -s -o /dev/null -w '%{{http_code}}' --max-time 10 {ORTHANC_URL}/")
    code = code.strip()
    auth = (
        Check("orthanc requires auth", True, "401 without credentials")
        if code == "401"
        else Check("orthanc requires auth", False, f"expected 401 from {ORTHANC_URL}/, got {code or 'no answer'}")
    )
    return seeded, auth


def check_cstore(run_smoke: Callable[[], tuple[int, str, str]]) -> Check:
    """A real C-STORE from Orthanc into XNAT: smoke-cstore.sh's verdict and its last word.

    The smoke speaks on stdout; stderr carries kubectl's own chatter ("Defaulted container ..."),
    so it is read only when the smoke said nothing at all.
    """
    code, stdout, stderr = run_smoke()

    def last_line(text: str) -> str:
        lines = [ANSI.sub("", line).strip() for line in text.splitlines() if line.strip()]
        return lines[-1] if lines else ""

    return Check("xnat c-store", code == 0, last_line(stdout) or last_line(stderr) or f"smoke-cstore exited {code}")


# A curl config line carrying a credential as a Basic auth header. Every call below runs in the
# xnat-web pod and hands curl its credential this way, through a pipe, so no process list in the
# pod shows it. XNAT's comes from the service user the pod already holds in its environment and
# never crosses kubectl; Orthanc's arrives on stdin. Base64 leaves nothing to quote: a credential
# holding a quote or a newline cannot end the config line early, so curl never parses part of it
# as a directive or echoes it back in a warning, which would land in the report.
CURL_AUTH = (
    r"""curl_auth() { printf 'header = "Authorization: Basic %s"\n' "$(printf '%s' "$1" | base64 | tr -d '\n')"; }"""
)
XNAT_CURL = 'curl_auth "$XNAT_SERVICE_USER:$XNAT_SERVICE_PASSWORD" | curl -sS -K - --max-time 100 -w "\\n%{http_code}"'
ORTHANC_CURL = 'curl_auth "$ocreds" | curl -fsS -K - --max-time 100'


class XnatPod:
    """XNAT's REST API and the mocked PACS, both reached from inside the xnat-web pod."""

    def __init__(self, cluster: Cluster, pod: str) -> None:
        self.cluster = cluster
        self.pod = pod

    def _xnat(self, curl_args: str, before: str = "", stdin: str | None = None) -> tuple[int, str]:
        """Run one XNAT call: (HTTP status, body), status 0 when XNAT gave no answer."""
        ok, out = self.cluster.exec(self.pod, f"{CURL_AUTH}\n{before}{XNAT_CURL} {curl_args}", stdin=stdin)
        if not ok:
            return 0, out
        body, _, code = out.rpartition("\n")
        return (int(code), body) if code.isdigit() else (0, out)

    def request(self, method: str, path: str, body: str | None = None, content_type: str = "") -> tuple[int, str]:
        data = f"-H {shlex.quote('Content-Type: ' + content_type)} --data-binary {shlex.quote(body)} " if body else ""
        return self._xnat(f"-X {method} {data}{shlex.quote(XNAT_URL + path)}")

    def smallest_series(self, orthanc_creds: str) -> tuple[str, int] | None:
        """The Orthanc series with the fewest instances among the first SERIES_SAMPLE, and its size."""
        query = json.dumps({"Level": "Series", "Query": {}, "Limit": SERIES_SAMPLE, "Expand": True})
        script = (
            f"{CURL_AUTH}\nread -r ocreds\n{ORTHANC_CURL} -X POST --data-binary {shlex.quote(query)} "
            f"{ORTHANC_URL}/tools/find"
        )
        ok, out = self.cluster.exec(self.pod, script, stdin=orthanc_creds)
        try:
            series = [(s["ID"], len(s.get("Instances", []))) for s in json.loads(out)] if ok else []
        except (json.JSONDecodeError, TypeError, KeyError):
            series = []
        series = [s for s in series if s[1] > 0]
        return min(series, key=lambda s: s[1]) if series else None

    def import_series(self, orthanc_creds: str, series_id: str, project: str) -> tuple[int, str]:
        """Copy one series from Orthanc into the project's archive, through XNAT's import service.

        ``action=commit`` with ``AA=true`` has the DICOM-zip importer build the session and archive it
        before it answers, with the archive URI as the body. Without them the session waits in the
        prearchive until XNAT's idle timer archives it, about five minutes later.
        """
        fetch = (
            "read -r ocreds\n"
            "zip=$(mktemp)\n"
            "trap 'rm -f \"$zip\"' EXIT\n"
            f'{ORTHANC_CURL} -o "$zip" {shlex.quote(f"{ORTHANC_URL}/series/{series_id}/archive")} '
            "|| { echo 'could not fetch the series from Orthanc' >&2; exit 1; }\n"
        )
        url = (
            f"{XNAT_URL}/data/services/import?import-handler=DICOM-zip&PROJECT_ID={project}"
            "&SUBJECT_ID=selftest&EXPT_LABEL=selftest&inbody=true&action=commit&AA=true"
        )
        return self._xnat(
            f"-X POST -H 'Content-Type: application/zip' --data-binary @\"$zip\" {shlex.quote(url)}",
            before=fetch,
            stdin=orthanc_creds,
        )


def _results(body: str) -> list[dict]:
    """The rows of an XNAT ResultSet answer, or none."""
    try:
        rows = json.loads(body)["ResultSet"]["Result"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return []
    return rows if isinstance(rows, list) else []


def _project_xml(project: str) -> str:
    """The projectData document imaging-api posts to create a project (services/projects.py)."""
    root = ET.Element(f"{{{XNAT_URL}/data/projects}}projectData")
    ET.SubElement(root, "ID").text = project
    ET.SubElement(root, "secondary_ID").text = project
    ET.SubElement(root, "name").text = SELFTEST_NAME
    ET.SubElement(root, "description").text = "Created and deleted by make selftest (selftest_k8s.py)"
    return ET.tostring(root, encoding="unicode", short_empty_elements=False)


def check_conversion(
    xnat: XnatPod,
    orthanc_creds: str | None,
    timeout: int,
    poll: int = 10,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Check:
    """dcm2niix turns a stored series into NIfTI on this cluster (FLIP#1448).

    A C-STORE proves XNAT received a study, not that the Container Service can convert it: on
    Kubernetes the conversion is a Job of its own, which has to be admitted, scheduled, mount the
    XNAT volume and hand its output back. So import the smallest series Orthanc holds into a
    throwaway project, launch dcm2niix on its scan the way a cohort pull's event subscription
    does, and require a Complete container and a NIfTI file on the scan. The project is deleted
    afterwards, whatever the outcome.
    """
    name = "dcm2niix conversion"
    if not orthanc_creds:
        return Check(name, False, "could not read an Orthanc user from its Secret")
    status, body = xnat.request("GET", "/xapi/commands?name=dcm2niix")
    try:
        command = json.loads(body)[0]
        command_id, wrapper = command["id"], command["xnat"][0]["name"]
    except (json.JSONDecodeError, LookupError, TypeError):
        return Check(name, False, f"no dcm2niix command is registered (HTTP {status}: {body[:120]})")
    series = xnat.smallest_series(orthanc_creds)
    if series is None:
        return Check(name, False, "Orthanc lists no series to convert")

    # A project left behind by an interrupted run would hold its old scan, so clear it first. Only
    # one this check created, though: the service user is an XNAT administrator, and a project
    # that merely shares the id is someone else's to remove.
    status, body = xnat.request("GET", "/data/projects?format=json")
    if status != 200:
        return Check(name, False, f"could not list XNAT's projects (HTTP {status}: {body[:120]})")
    existing = next((p for p in _results(body) if p.get("ID") == SELFTEST_PROJECT), None)
    if existing is not None:
        if existing.get("name") != SELFTEST_NAME:
            return Check(
                name,
                False,
                f"XNAT already holds a {SELFTEST_PROJECT} project this self-test did not create "
                f"(named {existing.get('name')!r}); remove or rename it, then rerun",
            )
        status, body = xnat.request("DELETE", f"/data/projects/{SELFTEST_PROJECT}?removeFiles=true")
        if status != 200:
            return Check(name, False, f"could not clear the {SELFTEST_PROJECT} an earlier run left (HTTP {status})")
    status, body = xnat.request("POST", "/data/projects", _project_xml(SELFTEST_PROJECT), "application/xml")
    if status != 200:
        return Check(name, False, f"could not create the {SELFTEST_PROJECT} project (HTTP {status}: {body[:120]})")
    try:
        result = _convert(xnat, orthanc_creds, series, command_id, wrapper, timeout, poll, sleep, clock)
    finally:
        cleanup, body = xnat.request("DELETE", f"/data/projects/{SELFTEST_PROJECT}?removeFiles=true")
    if cleanup != 200:
        detail = f"{result.detail}; could not delete the {SELFTEST_PROJECT} project (HTTP {cleanup}), delete it in XNAT"
        return Check(name, result.ok, detail)
    return result


def _convert(
    xnat: XnatPod,
    orthanc_creds: str,
    series: tuple[str, int],
    command_id: int,
    wrapper: str,
    timeout: int,
    poll: int,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> Check:
    name = "dcm2niix conversion"
    project = SELFTEST_PROJECT
    series_id, instances = series
    # The import archives before it answers, so an archive XNAT refuses (a validation error, say)
    # comes back here as the import's own failure.
    status, body = xnat.import_series(orthanc_creds, series_id, project)
    if status != 200:
        return Check(name, False, f"XNAT did not import Orthanc series {series_id} (HTTP {status}: {body[:200]})")
    experiments = _results(xnat.request("GET", f"/data/projects/{project}/experiments?format=json")[1])
    if not experiments:
        return Check(
            name,
            False,
            f"XNAT answered the import of Orthanc series {series_id} ({body.strip()[:120]!r}) "
            f"but archived no session into {project}",
        )
    experiment = experiments[0]["ID"]
    scans = _results(xnat.request("GET", f"/data/experiments/{experiment}/scans?format=json")[1])
    if not scans:
        return Check(name, False, f"session {experiment} has no scan")
    scan = scans[0]["ID"]

    enable = f"/xapi/projects/{project}/commands/{command_id}/wrappers/{wrapper}/enabled"
    status, body = xnat.request("PUT", enable)
    if status != 200:
        return Check(name, False, f"could not enable dcm2niix on {project} (HTTP {status}: {body[:120]})")
    launch = f"/xapi/projects/{project}/commands/{command_id}/wrappers/{wrapper}/root/scan/launch"
    target = json.dumps({"scan": f"/archive/experiments/{experiment}/scans/{scan}"})
    status, body = xnat.request("POST", launch, target, "application/json")
    try:
        workflow = json.loads(body).get("workflow-id") if status == 200 else None
    except (json.JSONDecodeError, AttributeError):
        workflow = None
    if not workflow:
        return Check(name, False, f"the Container Service refused the launch (HTTP {status}: {body[:160]})")

    started = clock()
    last = "no container yet"
    while True:
        _, body = xnat.request("GET", f"/xapi/projects/{project}/containers")
        try:
            containers = [c for c in json.loads(body) if isinstance(c, dict)]
        except (json.JSONDecodeError, TypeError):
            containers = []
        # Only this launch's own container counts. XNAT keeps container rows after a project is deleted, so
        # the re-created FLIPSELFTEST lists earlier runs' rows, and ours appears only once its Job exists.
        mine = [c for c in containers if c.get("workflow-id") == workflow]
        if mine:
            last = mine[0].get("status") or "no status"
        if last.startswith(("Complete", "Failed", "Killed")):
            break
        if clock() - started >= timeout:
            return Check(
                name,
                False,
                f"dcm2niix had not finished after {timeout}s (last status: {last}). If other conversions "
                "are queued, `kubectl describe resourcequota` shows the cap is full; rerun when they finish",
            )
        sleep(poll)
    elapsed = int(clock() - started)
    if not last.startswith("Complete"):
        return Check(name, False, f"dcm2niix ended {last} after {elapsed}s on scan {scan} of session {experiment}")

    files = _results(
        xnat.request("GET", f"/data/experiments/{experiment}/scans/{scan}/resources/NIFTI/files?format=json")[1]
    )
    nifti = [f.get("Name", "") for f in files if str(f.get("Name", "")).endswith((".nii", ".nii.gz"))]
    if not nifti:
        return Check(name, False, f"dcm2niix completed but scan {scan} has no NIfTI file")
    return Check(name, True, f"a {instances}-instance series converted to {nifti[0]} in {elapsed}s")


def check_fl_client(cluster: Cluster, settle_seconds: int, sleep: Callable[[float], None] = time.sleep) -> Check:
    """The FL client stays up. One look proves nothing (a crash-looping pod is briefly Running),
    so sample twice a settle period apart: running both times, no restart in between."""
    pod = cluster.pod("fl-client")
    if not pod:
        return Check("fl client running", False, "no running fl-client pod")
    first = cluster.container_state(pod)
    sleep(settle_seconds)
    second = cluster.container_state(pod)
    if first[0] and second[0] and second[1] == first[1]:
        return Check("fl client running", True, f"{pod} steady over {settle_seconds}s (no FL server is needed)")
    return Check("fl client running", False, f"{pod} not steady: running/restarts {first} -> {second}")


# ── the report ──────────────────────────────────────────────────────────────────────


def write_report(checks: list[Check], out: Path, backend: str, started: str, finished: str, stamp: str) -> bool:
    """Write the JSON and markdown report in the node self-test's shape; return whether all passed."""
    ok = bool(checks) and all(c.ok for c in checks)
    report = {
        "shape": "kubernetes",
        "backend": backend,
        "started": started,
        "finished": finished,
        "ok": ok,
        "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in checks],
    }
    out.mkdir(parents=True, exist_ok=True)
    base = out / f"selftest-k8s-{backend}-{stamp}"
    base.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
    rows = "\n".join(f"| {'✅' if c.ok else '❌'} | {c.name} | {c.detail} |" for c in checks)
    status = "PASSED" if ok else "FAILED"
    md = (
        f"# Kubernetes trust self-test: {backend}\n\n{status} · {started} → {finished}\n\n"
        f"| | Check | Detail |\n|---|---|---|\n{rows}\n"
    )
    base.with_suffix(".md").write_text(md)
    (out / f"latest-k8s-{backend}.md").write_text(md)
    print(f"Report: {base}.md")
    return ok


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--namespace", default="flip-trust")
    parser.add_argument("--release", default="trust-release")
    parser.add_argument("--context", default=None)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--fl-settle-seconds", type=int, default=45)
    # A first conversion on a node pulls the dcm2niix image before it can start.
    parser.add_argument("--conversion-timeout", type=int, default=600)
    args = parser.parse_args(argv)

    cluster = KubectlCluster(args.namespace, args.release, args.context)
    values = cluster.values()
    if values is None:
        print(f"❌ no release {args.release} in {args.namespace} (helm get values failed)", file=sys.stderr)
        return 2
    backend = values.get("flBackend", "nvflare")
    fl_enabled = values.get("flClient", {}).get("enabled", True)

    started, stamp = now(), datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    print(f"== Kubernetes self-test ({backend}) of {args.release} in {args.namespace}, started {started}")
    checks: list[Check] = []

    def record(check: Check) -> None:
        checks.append(check)
        print(f"  ✅ {check.name}" if check.ok else f"  ❌ {check.name}: {check.detail}")

    def run_smoke() -> tuple[int, str, str]:
        env = {
            **os.environ,
            "NAMESPACE": args.namespace,
            "RELEASE_NAME": args.release,
            "KUBE_CONTEXT": args.context or "",
        }
        result = subprocess.run(
            ["bash", str(SCRIPTS_DIR / "smoke-cstore.sh")], env=env, capture_output=True, text=True, timeout=600
        )
        return result.returncode, result.stdout, result.stderr

    record(check_release_ready(cluster))
    if fl_enabled:
        record(check_fl_kit(cluster, backend))
    for component in API_COMPONENTS:
        record(check_health(cluster, component))
    record(check_omop_seeded(cluster))
    for check in check_orthanc(cluster):
        record(check)
    record(check_cstore(run_smoke))
    xnat_pod = cluster.pod("xnat-web")
    if xnat_pod:
        record(check_conversion(XnatPod(cluster, xnat_pod), cluster.orthanc_credentials(), args.conversion_timeout))
    else:
        record(Check("dcm2niix conversion", False, "no running xnat-web pod"))
    if fl_enabled:
        record(check_fl_client(cluster, args.fl_settle_seconds))

    return 0 if write_report(checks, args.out, backend, started, now(), stamp) else 1


if __name__ == "__main__":
    sys.exit(main())
