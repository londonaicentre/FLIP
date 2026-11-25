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
"""Prove the egress and drift checks on a throwaway release (FLIP#2079): the kind job of test_helm_chart.yml.

A check that always passes proves nothing, so each is run against a node it must pass and a node it must not:

1. the release as installed: no drift; its network is open, which the egress row must report as a warning
2. one Deployment changed by hand: the drift row must fail and name it
3. the change undone: the drift row must pass again
4. the release upgraded to a policy that allows DNS only: the egress row must pass, which also shows that this
   cluster enforces NetworkPolicy at all

Each run's report is kept under --out. The CI release has no XNAT and no FL client, so the self-test's other rows
fail there and are not this script's business: it reads the two rows it is about.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

CHART = Path(__file__).resolve().parents[1]
EGRESS, DRIFT = "undeclared destinations blocked", "no configuration drift"
# Pods may resolve names and reach nothing else outside the namespace.
DNS_ONLY = [
    "--set-json",
    'networkPolicies.allowedEgressPorts=[{"port":53,"protocol":"UDP"},{"port":53,"protocol":"TCP"}]',
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--namespace", default="flip-trust")
    parser.add_argument("--release", default="trust-release")
    parser.add_argument("--context", default=None)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    context = ["--context", args.context] if args.context else []
    kubectl = ["kubectl", *context, "-n", args.namespace]
    helm = ["helm", *(["--kube-context", args.context] if args.context else []), "-n", args.namespace]
    deployment = f"deployment/{args.release}-flip-trust-trust-api"
    failures: list[str] = []

    def run(*command: str) -> None:
        print("+", " ".join(command), flush=True)
        subprocess.run(command, check=True)

    def rows(case: str) -> dict[str, dict]:
        """Run the self-test and return its rows by name. Its exit code is not read: other rows fail here."""
        out = args.out / case
        subprocess.run(
            [sys.executable, str(CHART / "scripts" / "selftest_k8s.py"), *context, "--namespace", args.namespace]
            + ["--release", args.release, "--out", str(out), "--fl-settle-seconds", "0", "--conversion-timeout", "30"],
            check=False,
        )
        report = json.loads(next(out.glob("selftest-k8s-*.json")).read_text())
        return {row["name"]: row for row in report["checks"]}

    def expect(case: str, row: dict, status: str, says: str = "") -> None:
        ok = row["status"] == status and says in row["detail"]
        print(
            f"{'✅' if ok else '❌'} {case}: {row['name']} is {row['status']} (wanted {status}): {row['detail'][:200]}"
        )
        if not ok:
            failures.append(f"{case}: {row['name']}")

    installed = rows("1-installed")
    expect("as installed", installed[DRIFT], "pass")
    expect("as installed", installed[EGRESS], "warn", "example.com")

    run(*kubectl, "set", "env", deployment, "CHANGED_BY_HAND=1")
    run(*kubectl, "rollout", "status", deployment, "--timeout=180s")
    expect("a Deployment changed by hand", rows("2-changed")[DRIFT], "fail", "env[CHANGED_BY_HAND]")
    run(*kubectl, "set", "env", deployment, "CHANGED_BY_HAND-")
    run(*kubectl, "rollout", "status", deployment, "--timeout=180s")
    expect("the change undone", rows("3-undone")[DRIFT], "pass")

    # The sample data is loaded already, and its loader could not download anything through this policy.
    seed_off = ["--set", "trustData.seed.enabled=false"]
    run(*helm, "upgrade", args.release, str(CHART), "--reuse-values", *DNS_ONLY, *seed_off, "--wait", "--timeout", "5m")
    confined = rows("4-dns-only")
    expect("egress allows DNS only", confined[EGRESS], "pass", "a sample, not a proof")
    expect("egress allows DNS only", confined[DRIFT], "pass")

    print("\n" + ("All proven." if not failures else "Not proven: " + "; ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
