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

The Kubernetes form of the VM node self-test (FLIP#1406), the acceptance checklist
every trust shape runs: the release is ready, the FL client has its kit, the three APIs are
healthy, OMOP and Orthanc are seeded, Orthanc refuses anonymous requests, XNAT accepts a real
C-STORE (smoke-cstore.sh), dcm2niix converts a stored series to NIfTI, and the FL client stays
up. A check that needs sample data the release does not load passes as skipped, saying why. It needs
no hub: an unreachable hub or FL server is not a failure. It runs from the
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
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR.parents[1] / "scripts"))

import node_checks  # noqa: E402 — the checks themselves, shared with the Compose node (FLIP#1449)
import trust_checks  # noqa: E402 — the result type and report every trust check shares
from node_checks import (  # noqa: E402, F401 — re-exported: the checks are reached through this module too
    XnatPod,
    _convert,
    check_conversion,
    check_cstore,
    check_fl_client,
    check_fl_kit,
    check_health,
    check_omop_seeded,
    check_orthanc,
    nvflare_startup_dir,
)
from trust_checks import Check, failed, passed  # noqa: E402


class Cluster(node_checks.Node, Protocol):
    """A node that is a Helm release: the shared checks' Node, plus the workloads its readiness is read from."""

    def workloads(self) -> list[dict]: ...
    def manifest_diff(self) -> tuple[int, str]: ...


def _is_ready(pod: dict) -> bool:
    conditions = pod.get("status", {}).get("conditions", [])
    return any(c.get("type") == "Ready" and c.get("status") == "True" for c in conditions)


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
        # A terminating pod still reports Running; never test the revision being replaced.
        items = [i for i in (found or {}).get("items", []) if not i["metadata"].get("deletionTimestamp")]
        ready = [i for i in items if _is_ready(i)]
        return (ready or items)[0]["metadata"]["name"] if items else None

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

    def mounts(self, pod: str, container: str) -> list[dict]:
        containers = self._pod_json(pod).get("spec", {}).get("containers", [])
        return next((c.get("volumeMounts", []) for c in containers if c.get("name") == container), [])

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

    def manifest_diff(self) -> tuple[int, str]:
        """`kubectl diff` of the release's own manifest against the cluster: (exit code, output).

        0 nothing differs, 1 something does, anything else the comparison did not run. A server-side dry run: it
        asks the API server what applying the manifest would change, and changes nothing.
        """
        ok, manifest = self._run([*self.helm, "get", "manifest", self.release])
        if not ok or not manifest.strip():
            return 2, f"helm get manifest: {manifest[:200] or 'empty'}"
        try:
            result = subprocess.run(
                [*self.kubectl, "diff", "-f", "-"], input=manifest, capture_output=True, text=True, timeout=300
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            return 2, str(e)
        return result.returncode, result.stdout if result.returncode == 1 else result.stderr

    def exec(self, pod: str, script: str, stdin: str | None = None) -> tuple[bool, str]:
        return self._run(
            [*self.kubectl, "exec", *(["-i"] if stdin is not None else []), pod, "--", "sh", "-c", script], stdin=stdin
        )


# ── the checks ──────────────────────────────────────────────────────────────────────


def rollout_gap(item: dict) -> str | None:
    """Why a workload has not finished rolling out, or None (the conditions `kubectl rollout status` waits for).

    Ready replicas alone are not enough: during an upgrade of a one-replica Deployment the old pod
    stays Ready while the new one crash-loops, so the count is met by the revision being replaced.
    """
    spec, status = item.get("spec", {}), item.get("status", {})
    if status.get("observedGeneration", 0) < item["metadata"].get("generation", 0):
        return "spec not yet observed"
    if item["kind"] == "DaemonSet":
        want = status.get("desiredNumberScheduled", 0)
        updated, ready = status.get("updatedNumberScheduled", 0), status.get("numberReady", 0)
    else:
        want = spec.get("replicas", 1)
        updated, ready = status.get("updatedReplicas", 0), status.get("readyReplicas", 0)
        if status.get("replicas", 0) > updated:
            return f"{status['replicas'] - updated} old replica(s) still running"
        if item["kind"] == "StatefulSet" and status.get("currentRevision") != status.get("updateRevision"):
            return "update revision not yet current"
    if updated < want:
        return f"{updated}/{want} updated"
    if ready < want:
        return f"{ready}/{want} ready"
    return None


def check_release_ready(cluster: Cluster) -> Check:
    """Every Deployment, StatefulSet and DaemonSet of the release has rolled out and is ready."""
    items = cluster.workloads()
    if not items:
        return failed("services ready", "the release has no workloads in this namespace")
    short = [f"{item['metadata']['name']} {gap}" for item in items if (gap := rollout_gap(item))]
    if short:
        return failed("services ready", "not ready: " + ", ".join(short))
    return passed("services ready", f"{len(items)} workloads ready")


DIFFED = re.compile(r"^diff -u.* \S*/LIVE-[^/]+/(\S+)", re.M)


def check_drift(cluster: Cluster) -> Check:
    """Every object of the release still matches the manifest Helm deployed (FLIP#2078).

    Names the objects that differ and never prints the difference: it can hold a Secret's content.
    """
    code, out = cluster.manifest_diff()
    if code not in (0, 1):
        raise RuntimeError(f"kubectl diff did not run: {out.strip()[:200]}")
    differing = []
    for path in DIFFED.findall(out) if code == 1 else []:
        parts = path.split(".")  # <group>.<version>.<Kind>.<namespace>.<name>, the group absent for core kinds
        kind = next((i for i, part in enumerate(parts) if part[:1].isupper()), 0)
        differing.append(f"{parts[kind]} {'.'.join(parts[kind + 2 :])} differs from the release's manifest")
    if code == 1 and not differing:
        raise RuntimeError(f"kubectl diff reported a difference this check could not read: {out[:200]}")
    return node_checks.drift(differing, "every object of the release matches its Helm manifest (kubectl diff)")


def run_smoke(namespace: str, release: str, context: str | None) -> tuple[int, str, str]:
    """smoke-cstore.sh's exit code and output; a smoke that cannot start or overruns is a failure, not a crash."""
    env = {**os.environ, "NAMESPACE": namespace, "RELEASE_NAME": release, "KUBE_CONTEXT": context or ""}
    try:
        result = subprocess.run(
            ["bash", str(SCRIPTS_DIR / "smoke-cstore.sh")], env=env, capture_output=True, text=True, timeout=600
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, "", str(e)
    return result.returncode, result.stdout, result.stderr


# ── the report ──────────────────────────────────────────────────────────────────────


def write_report(checks: list[Check], out: Path, backend: str, started: str, finished: str, stamp: str) -> None:
    """Write the JSON and markdown report, in the shape every trust check shares."""
    path = trust_checks.write_report(
        checks,
        out,
        stem=f"selftest-k8s-{backend}-{stamp}",
        latest=f"latest-k8s-{backend}",
        title=f"Kubernetes trust self-test: {backend}",
        started=started,
        finished=finished,
        shape="kubernetes",
        backend=backend,
    )
    print(f"Report: {path}")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def missing_sample_data(values: dict) -> tuple[str, str]:
    """Why OMOP, and why Orthanc, hold no sample data for the checks that need it: empty when the release seeds it.

    A real trust turns the seed off (trustData.seed.enabled, from the kit's TRUST_DATA_SEED, FLIP#1687), and the
    chart seeds Orthanc only with trustData.seed.orthanc, which is off by default.
    """
    seed = values.get("trustData", {}).get("seed", {})
    if not seed.get("enabled", True):
        reason = "trustData.seed.enabled=false, so the release holds no sample data"
        return reason, reason
    omop = "" if seed.get("omop", True) else "trustData.seed.omop=false, so OMOP holds no sample data"
    orthanc = "" if seed.get("orthanc", False) else "trustData.seed.orthanc=false, so Orthanc holds no sample studies"
    return omop, orthanc


def run_checks(
    cluster: Cluster,
    values: dict,
    smoke: Callable[[], tuple[int, str, str]],
    conversion_timeout: int,
    fl_settle_seconds: int,
    hub: bool = False,
) -> list[Check]:
    """The shared node checks against this release; a check with no data to work on is skipped."""
    no_omop, no_orthanc = missing_sample_data(values)
    return node_checks.run_checks(
        cluster,
        backend=values.get("flBackend", "nvflare"),
        fl_enabled=values.get("flClient", {}).get("enabled", True),
        ready=lambda: check_release_ready(cluster),
        smoke=smoke,
        no_omop=no_omop,
        no_orthanc=no_orthanc,
        no_hub="" if hub else "not asked for: --hub checks the heartbeat of a release joined to a hub",
        find_drift=lambda: check_drift(cluster),
        conversion_timeout=conversion_timeout,
        fl_settle_seconds=fl_settle_seconds,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--namespace", default="flip-trust")
    parser.add_argument("--release", default="trust-release")
    parser.add_argument("--context", default=None)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--fl-settle-seconds", type=int, default=45)
    parser.add_argument("--hub", action="store_true", help="also check trust-api's heartbeat to the hub")
    # A first conversion on a node pulls the dcm2niix image before it can start.
    parser.add_argument("--conversion-timeout", type=int, default=600)
    args = parser.parse_args(argv)

    cluster = KubectlCluster(args.namespace, args.release, args.context)
    values = cluster.values()
    if values is None:
        print(f"❌ no release {args.release} in {args.namespace} (helm get values failed)", file=sys.stderr)
        return 2
    backend = values.get("flBackend", "nvflare")

    started, stamp = now(), datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    print(f"== Kubernetes self-test ({backend}) of {args.release} in {args.namespace}, started {started}")
    checks = run_checks(
        cluster,
        values,
        lambda: run_smoke(args.namespace, args.release, args.context),
        args.conversion_timeout,
        args.fl_settle_seconds,
        args.hub,
    )
    write_report(checks, args.out, backend, started, now(), stamp)
    return trust_checks.exit_code(checks)


if __name__ == "__main__":
    sys.exit(main())
