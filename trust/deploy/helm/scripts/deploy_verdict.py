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

"""Decide what a non-zero `helm upgrade` actually meant, from cluster evidence (FLIP#1645).

WHY THIS EXISTS

`deploy-trust-k8s` used to carry this, in a shell comment:

    deploy returned non-zero — often a non-fatal post-install hook (e.g. xnat-init
    — see #565), but it may be a genuine Helm failure.

That is a judgement a human makes by reading the output. Automation cannot make it, and the
auto-upgrade agent (FLIP#1640) has to: it either treats every non-zero exit as a failure, which
raises a false alarm on most upgrades, or ignores it, and misses the upgrades that genuinely did
not land. The ambiguity is removed here rather than coded around.

WHAT MAKES THE TWO CASES DISTINGUISHABLE

Helm applies the new manifests BEFORE it runs post-upgrade hooks. So:

* a failure that left the release revision where it was means Helm refused the change and the
  cluster still runs the old spec — nothing was deployed, and that is a genuine failure;
* a failure on a revision that DID advance means the new spec is live, and what failed afterwards
  is the hook or a rollout wait.

The revision alone is not enough for the second case: FLIP#1228 was a correct chart that never
reached the running pod, and "the pods are Ready" was true throughout — they were the OLD pods.
So a workload counts as rolled out only when its controller has observed the new generation
(`observedGeneration >= generation`) and every replica is both updated and ready. A stale
controller reading Ready is treated as not rolled out, which is the conservative direction.

VERDICTS

0  PASS        — helm exited 0, or it exited non-zero on an advanced revision with every workload
                 rolled out and the init job complete (its wait expired; the release converged
                 anyway, and the evidence for that is complete).
1  FAILED      — the spec never applied, a workload did not roll out, or the evidence needed to
                 tell those apart could not be gathered. Fail closed: an unreadable cluster is
                 never reported as a successful upgrade.
3  HOOK_FAILED — the spec applied and every workload rolled out, but the `xnat-init` hook did not
                 complete. The trust is on the new release; its XNAT configuration step needs
                 re-running (`make xnat-init`). Distinct from 1 so an unattended upgrade can
                 report "upgraded, init incomplete" instead of guessing.

3 is not a pass. It is non-zero and `make` fails on it; it simply says something different from 1.

`decide()` takes a plain dict of evidence and returns a verdict, and `gather()` fills that dict
from helm and kubectl, so the decision is testable without a cluster.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass

PASS = 0
FAILED = 1
HOOK_FAILED = 3

# The chart labels the init job's resources with this component. The same selector the Makefile's
# `xnat-init` target and the hook template use.
XNAT_INIT_COMPONENT = "xnat-init"


@dataclass(frozen=True)
class Verdict:
    """One decision, with the sentence an operator or an agent reads."""

    code: int
    reason: str
    detail: str


def _rolled_out(workload: dict) -> bool:
    """Has this workload's controller actually brought up the new spec?

    `Ready` on its own is what made FLIP#1228 invisible: the old pod was Ready for five
    consecutive upgrades. A controller that has not yet observed the new generation is reported as
    not rolled out, so the uncertainty counts against the upgrade rather than for it.
    """
    generation = workload.get("generation")
    observed = workload.get("observedGeneration")
    if generation is None or observed is None or observed < generation:
        return False
    desired = workload.get("desired")
    if desired is None:
        return False
    return workload.get("updated") == desired and workload.get("ready") == desired


def decide(evidence: dict) -> Verdict:
    """Classify a deploy from its evidence. See the module docstring for the verdicts."""
    if evidence.get("helm_rc") == 0:
        return Verdict(PASS, "helm-ok", "helm upgrade exited 0.")

    revision_before = evidence.get("revision_before")
    revision_after = evidence.get("revision_after")

    # Fail closed. Without the revision there is no way to tell an un-applied spec from a failed
    # hook, and the safe reading of "I could not look" is "it failed".
    if revision_after is None:
        return Verdict(
            FAILED,
            "no-release-state",
            "helm upgrade failed and the release's state could not be read, so it is not "
            "possible to tell whether the new spec was applied. Treating it as a failure.",
        )

    # A first install that never got as far as creating a revision.
    if revision_before is None and revision_after == 0:
        return Verdict(FAILED, "not-applied", "helm upgrade failed before creating a release revision.")

    if revision_before is not None and revision_after <= revision_before:
        return Verdict(
            FAILED,
            "not-applied",
            f"helm upgrade failed and the release is still on revision {revision_after}: "
            "the new spec was never applied and the cluster runs the previous one.",
        )

    workloads = evidence.get("workloads")
    if not workloads:
        return Verdict(
            FAILED,
            "no-workload-state",
            f"the release advanced to revision {revision_after}, but its workloads could not be "
            "read, so the rollout cannot be confirmed. Treating it as a failure.",
        )

    stalled = sorted(w.get("name", "?") for w in workloads if not _rolled_out(w))
    if stalled:
        return Verdict(
            FAILED,
            "rollout-incomplete",
            f"the release advanced to revision {revision_after}, but these workloads have not "
            f"rolled out the new spec: {', '.join(stalled)}.",
        )

    if evidence.get("xnat_init_complete") is True:
        return Verdict(
            PASS,
            "converged-after-wait",
            f"helm's wait expired, but revision {revision_after} is live, every workload has "
            "rolled out and the xnat-init job completed. The upgrade landed.",
        )

    return Verdict(
        HOOK_FAILED,
        "xnat-init-incomplete",
        f"revision {revision_after} is live and every workload has rolled out, but the xnat-init "
        "job did not complete (see #565). The trust is on the new release; re-run its XNAT "
        "configuration with `make -C trust/deploy/helm xnat-init`, raising HELM_TIMEOUT if the "
        "job simply needed longer.",
    )


def _run(args: list[str]) -> tuple[bool, str]:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:  # pragma: no cover - environment failure
        return False, str(e)
    return result.returncode == 0, (result.stdout if result.returncode == 0 else result.stderr).strip()


def _json(args: list[str]) -> dict | list | None:
    ok, out = _run(args)
    if not ok:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def release_revision(helm: list[str], release: str) -> int | None:
    """The release's current revision, or None if there is no release / it cannot be read."""
    status = _json([*helm, "status", release, "-o", "json"])
    if not isinstance(status, dict):
        return None
    version = status.get("version")
    return version if isinstance(version, int) else None


def _workload_rows(items: list[dict]) -> list[dict]:
    rows = []
    for item in items:
        kind = item.get("kind", "")
        meta = item.get("metadata", {})
        spec = item.get("spec", {})
        status = item.get("status", {})
        if kind == "DaemonSet":
            desired = status.get("desiredNumberScheduled")
            updated = status.get("updatedNumberScheduled")
            ready = status.get("numberReady")
        else:
            desired = spec.get("replicas")
            updated = status.get("updatedReplicas", 0)
            ready = status.get("readyReplicas", 0)
        rows.append(
            {
                "name": f"{kind.lower()}/{meta.get('name', '?')}",
                "generation": meta.get("generation"),
                "observedGeneration": status.get("observedGeneration"),
                "desired": desired,
                "updated": updated,
                "ready": ready,
            }
        )
    return rows


def gather(helm: list[str], kubectl: list[str], release: str, helm_rc: int, revision_before: int | None) -> dict:
    """Collect the evidence `decide()` needs. Anything unreadable stays absent, and fails closed."""
    evidence: dict = {
        "helm_rc": helm_rc,
        "revision_before": revision_before,
        "revision_after": release_revision(helm, release),
    }
    if helm_rc == 0:
        return evidence

    workloads = _json(
        [
            *kubectl,
            "get",
            "deploy,statefulset,daemonset",
            "-l",
            f"app.kubernetes.io/instance={release}",
            "-o",
            "json",
        ]
    )
    if isinstance(workloads, dict) and isinstance(workloads.get("items"), list):
        evidence["workloads"] = _workload_rows(workloads["items"])

    jobs = _json(
        [
            *kubectl,
            "get",
            "job",
            "-l",
            f"app.kubernetes.io/instance={release},app.kubernetes.io/component={XNAT_INIT_COMPONENT}",
            "-o",
            "json",
        ]
    )
    if isinstance(jobs, dict) and isinstance(jobs.get("items"), list):
        items = jobs["items"]
        # The hook's delete policy is hook-succeeded, so a job that completed is usually already
        # gone: no job AND an advanced revision means it ran and was cleaned up. A job that is
        # still present has either failed or not finished.
        if not items:
            evidence["xnat_init_complete"] = True
        else:
            evidence["xnat_init_complete"] = any(
                any(
                    c.get("type") == "Complete" and c.get("status") == "True"
                    for c in j.get("status", {}).get("conditions", [])
                )
                for j in items
            )
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--release", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--context", default=None)
    parser.add_argument("--helm-rc", type=int, required=True, help="the exit code helm upgrade returned")
    parser.add_argument(
        "--revision-before",
        default="",
        help="the release revision before the upgrade; empty when there was no release",
    )
    parser.add_argument("--evidence-json", default=None, help="skip the cluster and classify this evidence (testing)")
    args = parser.parse_args(argv)

    if args.evidence_json:
        evidence = json.loads(args.evidence_json)
    else:
        context = ["--kube-context", args.context] if args.context else []
        helm = ["helm", *context, "-n", args.namespace]
        kubectl = ["kubectl", *(["--context", args.context] if args.context else []), "-n", args.namespace]
        before = int(args.revision_before) if args.revision_before.strip() else None
        evidence = gather(helm, kubectl, args.release, args.helm_rc, before)

    verdict = decide(evidence)
    if verdict.code == PASS:
        print(f"✅  deploy: {verdict.detail}")
    elif verdict.code == HOOK_FAILED:
        print(f"⚠️  deploy: {verdict.detail}", file=sys.stderr)
    else:
        print(f"❌  deploy: {verdict.detail}", file=sys.stderr)
    return verdict.code


if __name__ == "__main__":
    raise SystemExit(main())
