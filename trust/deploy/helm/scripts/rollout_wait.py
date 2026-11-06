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

"""Wait for the trust's CORE workloads to roll out, and only those (FLIP#1645).

`make deploy` needs a non-zero exit to mean "the upgrade did not land", which requires waiting
for the rollout: FLIP#1228 was a correct chart that never reached the running pod across five
upgrades, and an un-waited `helm upgrade` exits 0 for exactly that. Helm's own `--wait` does the
waiting properly, so the first implementation simply used it.

It cannot be used here, because `--wait` is all-or-nothing: Helm 3 offers no per-resource
exclusion, and ONE unschedulable pod fails the whole upgrade. Verified against a live cluster —
a single Pending pod alongside otherwise healthy workloads gives `UPGRADE FAILED: context
deadline exceeded`, exit 1, and leaves the release in `failed` state. Two supported trust
configurations sit permanently in that state by design:

* `flClient` on a scale-from-zero GPU node pool (values-aks.yaml) — Pending is its resting state
  between FL jobs, and that is the configuration the chart recommends to avoid billing a GPU
  that no job is using;
* `flClient` deployed before its kit is staged — Pending until the operator stages the kit.

Under `--wait` both turn every scheduled run of the auto-upgrade agent (FLIP#1640) into a failed
upgrade needing a human, which inverts the point of the agent. So the wait is scoped to the
workloads whose readiness actually means "the trust is serving", using the same readiness
semantics Helm applies (`kubectl rollout status` checks `observedGeneration` against
`generation`, the new ReplicaSet for a Deployment and `currentRevision == updateRevision` for a
StatefulSet).

The scope is a DENY list, not an allow list. A workload added to the chart tomorrow is waited on
unless it is named here, because the failure mode of forgetting to add a core service to an allow
list is silent — the deploy stops checking it and exits 0 regardless — while the failure mode of
forgetting to exclude an optional one is a loud, correctable timeout.

Excluded workloads are still REPORTED when they are not ready, so "not waited on" never means
"not looked at". That report never changes the exit code.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

# Workloads deliberately NOT waited on. Each entry needs a reason that says why being unready is a
# legitimate steady state for a supported configuration, rather than merely inconvenient.
OPTIONAL_COMPONENTS = {
    # Pending is the resting state on a scale-from-zero GPU node pool, and before the kit is
    # staged. Neither stops the trust serving data or imaging.
    "fl-client": "idle or awaiting its kit / GPU node",
    # Clinician-facing annotation UI; optional, and off by default (monailabel.enabled: false).
    "monailabel": "optional annotation service",
    # Observability. The trust serves fine while its dashboards are down, and failing an upgrade
    # because Grafana is unready would be a self-inflicted outage.
    "grafana": "observability",
    "loki": "observability",
    "alloy": "observability",
}

WORKLOAD_KINDS = ("deployment", "statefulset")


def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def discover(kubectl: str, namespace: str, release: str, timeout: int = 120) -> list[tuple[str, str, str]]:
    """Every chart-managed workload in the release, as (kind, name, component).

    Selecting on the release's own instance label keeps this to the chart's workloads, so a
    deploy is never held up by something else running in the namespace.
    """
    found: list[tuple[str, str, str]] = []
    for kind in WORKLOAD_KINDS:
        proc = _run(
            [
                kubectl,
                "get",
                kind,
                "--namespace",
                namespace,
                "--selector",
                f"app.kubernetes.io/instance={release}",
                "-o",
                "json",
            ],
            timeout=timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"could not list {kind}s in {namespace}: {proc.stderr.strip()}")
        try:
            payload = json.loads(proc.stdout or "{}")
        except (json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError(f"unreadable kubectl output for {kind}s: {exc}") from exc
        for item in payload.get("items") or []:
            meta = item.get("metadata") or {}
            name = meta.get("name")
            if not name:
                continue
            component = (meta.get("labels") or {}).get("app.kubernetes.io/component", "")
            found.append((kind, name, component))
    return found


def partition(
    workloads: list[tuple[str, str, str]],
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """Split into (waited, skipped). Anything not explicitly optional is waited on."""
    waited = [w for w in workloads if w[2] not in OPTIONAL_COMPONENTS]
    skipped = [w for w in workloads if w[2] in OPTIONAL_COMPONENTS]
    return waited, skipped


def rollout_status(kubectl: str, namespace: str, kind: str, name: str, timeout: str) -> tuple[bool, str]:
    proc = _run(
        [kubectl, "rollout", "status", f"{kind}/{name}", "--namespace", namespace, f"--timeout={timeout}"],
        timeout=_seconds(timeout) + 60,
    )
    detail = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    return proc.returncode == 0, detail


def is_ready(kubectl: str, namespace: str, kind: str, name: str) -> bool:
    """A point-in-time readiness check, used for the report on skipped workloads (no waiting)."""
    proc = _run(
        [kubectl, "get", kind, name, "--namespace", namespace, "-o", "json"],
        timeout=120,
    )
    if proc.returncode != 0:
        return False
    try:
        obj = json.loads(proc.stdout or "{}")
    except (json.JSONDecodeError, ValueError):
        return False
    status = obj.get("status") or {}
    spec = obj.get("spec") or {}
    desired = spec.get("replicas", 1)
    ready = status.get("readyReplicas", 0)
    return bool(desired == 0 or ready >= desired)


def _seconds(timeout: str) -> int:
    """Parse a Helm/kubectl duration ('30m', '600s', '1h') into seconds for the subprocess budget."""
    units = {"s": 1, "m": 60, "h": 3600}
    text = timeout.strip()
    if text and text[-1] in units:
        try:
            return int(float(text[:-1]) * units[text[-1]])
        except ValueError:
            pass
    try:
        return int(text)
    except ValueError:
        return 1800


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--timeout", default="30m", help="per-workload rollout budget")
    parser.add_argument("--kubectl", default="kubectl")
    args = parser.parse_args()

    try:
        workloads = discover(args.kubectl, args.namespace, args.release)
    except RuntimeError as exc:
        # Fail closed: if the rollout cannot be judged, the deploy has not been shown to have
        # landed, and saying otherwise is the FLIP#1228 failure again.
        print(f"could not determine the release's workloads: {exc}", file=sys.stderr)
        return 1

    if not workloads:
        print(f"no chart-managed workloads found for release {args.release}", file=sys.stderr)
        return 1

    waited, skipped = partition(workloads)

    failed: list[str] = []
    for kind, name, _component in sorted(waited):
        ok, detail = rollout_status(args.kubectl, args.namespace, kind, name, args.timeout)
        if ok:
            print(f"  ✓ {kind}/{name}")
        else:
            failed.append(f"{kind}/{name}: {detail}")
            print(f"  ✗ {kind}/{name}: {detail}")

    for kind, name, component in sorted(skipped):
        if not is_ready(args.kubectl, args.namespace, kind, name):
            reason = OPTIONAL_COMPONENTS.get(component, "optional")
            print(f"  - {kind}/{name} is not ready, not waited on ({reason})")

    if failed:
        print(
            f"\n{len(failed)} core workload(s) did not roll out within {args.timeout}.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
