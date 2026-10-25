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
#
"""Run a release's pre-release gates in order and record each as JSON (FLIP#1608).

``make release-gates TAG=v0.13.0 [INSTANCE=rel0130] [PR=<n>] [GATES=a,b] [CONTINUE=1] [KEEP=1]``

Gates, in this order, quickest feedback first: ``make unit_test`` and ``make integration_test`` (each suite starts
its own throwaway containers, so neither needs the instance), then both tutorial suites, all on a quiet host (no stack
up yet: beside a full stack, other tenants' load pushed flip-ui's first tests past their timeout); the instance
(``release-instance``); the FLARE smoke (after ``release-verify``); the switch to Flower (``restart-fl``, then verify
again); the Flower smoke, reusing the FLARE smoke's project so the images are pulled once; and teardown, which runs
even after a failure unless ``KEEP=1``.

Each gate writes ``release-gates/<TAG>/<gate>.json`` (commit, host, GPU, times, exit code, log path, metrics)
beside its log. A failed gate stops the run — later gates are recorded as not run — unless ``CONTINUE=1``; a
failed gate that ran against the stack also saves every instance container's log, before anything recreates them.
``make release-record`` turns the JSON into the release notes' *Release checks* section.

Run it from a dedicated release worktree. The instance writes ``trust/.env.<KIT>.development``, the kit names a
developer's own trusts use, so the run refuses to start if a kit there was written by anything but this instance.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import instance as instance_mod  # noqa: E402
import verify as verify_mod  # noqa: E402
from release_lib import (  # noqa: E402
    REPO,
    ReleaseError,
    assert_at_pr_head,
    assert_clean,
    default_instance,
    env_file_name,
    gates_dir,
    instance_env,
    instance_make_args,
    load_results,
)

ORDER = (
    "unit",
    "integration",
    "tutorials-nvflare",
    "tutorials-flower",
    "instance",
    "smoke-nvflare",
    "switch-flower",
    "smoke-flower",
    "teardown",
)


@dataclass
class Check:
    """A Python step: returns its problems, and an empty list passes."""

    fn: Callable[[], list[str]]


# A step is a command, a command built when its turn comes (it may need an earlier gate's result), or a check.
Step = list[str] | Callable[[], list[str]] | Check


@dataclass
class Gate:
    """One gate: its steps and how to read metrics out of its output."""

    name: str
    steps: list[Step]
    metrics: Callable[[list[tuple[float, str]]], dict] = field(default=lambda _lines: {})
    cleanup: Callable[[], list[str]] | None = None  # runs even when a step failed; returns what it did


SIMULATOR = re.compile(r"flower-superlink|flwr-simulation|flwr-serverapp")


def own_simulator_pids(ps_lines: list[str], repo: Path) -> list[int]:
    """PIDs of Flower simulator processes started from this checkout (``pgrep -af`` lines: ``<pid> <args>``).

    flwr keeps its local SuperLink running after a run, and ``sim-tutorial.sh`` refuses to run inside one another
    checkout started, so a release worktree left behind would block the next release's Flower tutorials.
    """
    prefix = f"{repo}/"
    pids = []
    for line in ps_lines:
        pid, _, args = line.strip().partition(" ")
        if pid.isdigit() and prefix in args and SIMULATOR.search(args):
            pids.append(int(pid))
    return pids


def stop_own_simulators() -> list[str]:
    """Stop this checkout's Flower simulator processes; never another checkout's."""
    ps = subprocess.run(
        ["pgrep", "-af", "flower-superlink|flwr-simulation|flwr-serverapp"], capture_output=True, text=True
    )
    stopped = []
    for pid in own_simulator_pids(ps.stdout.splitlines(), REPO):
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
            stopped.append(f"stopped simulator process {pid}")
    return stopped


def tutorial_metrics(lines: list[tuple[float, str]]) -> dict:
    """Per-tutorial pass/fail and seconds from ``run-all-tutorials`` output (one ``🚀 Running`` line each)."""
    runs: list[dict] = []
    for t, line in lines:
        start = re.search(r"🚀 Running (?:FLARE|Flower) tutorial (\S+)", line)
        if start:
            if runs:
                runs[-1].setdefault("seconds", round(t - runs[-1].pop("_t")))
            runs.append({"name": Path(start[1]).name, "passed": True, "_t": t})
        elif re.search(r"❌ tutorial \S+ failed", line) and runs:
            runs[-1]["passed"] = False
    if runs and "_t" in runs[-1]:
        runs[-1]["seconds"] = round(lines[-1][0] - runs[-1].pop("_t"))
    return {"tutorials": runs, "passed": sum(r["passed"] for r in runs), "total": len(runs)}


def smoke_metrics(lines: list[tuple[float, str]]) -> dict:
    """Project, model and final status: from the closing summary, or the project from its creation line."""
    out = {}
    for _t, line in lines:
        m = re.match(r"\s*(?:✅\s*)?(project_id|model_id|final_status)\s*=\s*(\S+)", line)
        if m:
            out[m[1]] = m[2]
    return out


# Gates that run against the stack: when one fails, its containers' logs are the evidence.
STACK_GATES = ("instance", "smoke-nvflare", "switch-flower", "smoke-flower")


def collect_logs(instance: str, logs: Path) -> list[str]:
    """Save every instance container's log, before a later gate or teardown recreates or removes them."""
    names = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}", "--filter", f"name=^{instance}-"],
        capture_output=True,
        text=True,
    ).stdout.split()
    logs.mkdir(parents=True, exist_ok=True)
    for name in names:
        with (logs / f"{name}.log").open("w") as f:
            subprocess.run(["docker", "logs", "--timestamps", name], stdout=f, stderr=subprocess.STDOUT)
    return names


def _run_command(cmd: list[str], log, env: dict[str, str], lines: list[tuple[float, str]]) -> int:
    """Run one command, streaming output to the log and keeping timed lines for the metric parsers."""
    log.write(f"▶ {' '.join(cmd)}\n")
    log.flush()
    with subprocess.Popen(cmd, cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.append((time.monotonic(), line.rstrip("\n")))
            log.write(line)
            log.flush()
    return proc.returncode


def _run_check(check: Check, log) -> int:
    with contextlib.redirect_stdout(log):
        try:
            return 1 if check.fn() else 0
        except (ReleaseError, subprocess.CalledProcessError, KeyError) as e:
            log.write(f"❌ {e}\n")
            return 1


def run_gate(gate: Gate, out_dir: Path, base: dict, env: dict[str, str]) -> dict:
    """Run a gate's steps until one fails; write and return its result."""
    log_path = out_dir / f"{gate.name}.log"
    lines: list[tuple[float, str]] = []
    start, t0, code = datetime.now(timezone.utc), time.monotonic(), 0
    with log_path.open("w") as log:
        for step in gate.steps:
            try:
                if isinstance(step, Check):
                    code = _run_check(step, log)
                else:
                    code = _run_command(step() if callable(step) else step, log, env, lines)
            except ReleaseError as e:
                log.write(f"❌ {e}\n")
                code = 1
            if code:
                break
        if gate.cleanup:
            for done in gate.cleanup():
                log.write(f"🧹 {done}\n")
    result = {
        **base,
        "gate": gate.name,
        "status": "passed" if code == 0 else "failed",
        "exit_code": code,
        "start": start.isoformat(timespec="seconds"),
        "end": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seconds": round(time.monotonic() - t0),
        "log": str(log_path),
        "metrics": gate.metrics(lines) if lines else {},
    }
    (out_dir / f"{gate.name}.json").write_text(json.dumps(result, indent=2) + "\n")
    print(f"{'✅' if code == 0 else '❌'} {gate.name} ({result['seconds']} s) → {log_path}", flush=True)
    return result


def skip_gate(name: str, out_dir: Path, base: dict) -> dict:
    """Record a gate the run did not reach, so a stale result from an earlier run cannot stand in for it."""
    result = {**base, "gate": name, "status": "not-run"}
    (out_dir / f"{name}.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def build_gates(tag: str, instance: str, project_id: Callable[[], str], port_offset: int = 10000) -> dict[str, Gate]:
    """The gates, each through the repo's own make targets and with the instance's settings."""
    args = instance_make_args(instance)

    def verified() -> list[str]:
        return verify_mod.verify(tag, instance)

    def nothing_left() -> list[str]:
        # Named in the log: the survivors are what the operator has to clean up by hand.
        left = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"name=^{instance}-", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        problems = [f"container {n} remains" for n in left]
        problems += [f"service {s['Name']} remains" for s in verify_mod.services(instance)]
        for line in problems:
            print(f"  ✗ {line}")
        return problems

    def smoke_flower() -> list[str]:
        # Reusing the FLARE smoke's project skips a second image pull; without one, the smoke creates its own.
        pid = project_id()
        return ["make", "e2e_smoke", "FL_BACKEND=flower", *([f"EXTRA_ARGS=--project-id {pid}"] if pid else []), *args]

    gates = [
        Gate("tutorials-nvflare", [["make", "-C", "fl-tutorials", "run-all-tutorials"]], tutorial_metrics),
        Gate(
            "tutorials-flower",
            [["make", "-C", "fl-tutorials", "run-all-tutorials", "FL_BACKEND=flower"]],
            tutorial_metrics,
            cleanup=stop_own_simulators,
        ),
        # flip-ui's tests run in a container on the instance's hub network; create-networks is idempotent.
        Gate("unit", [["make", "create-networks", *args], ["make", "unit_test", *args]]),
        Gate(
            "instance",
            [
                [sys.executable, str(REPO / "scripts" / "release" / "instance.py"), "--tag", tag]
                + ["--instance", instance, "--port-offset", str(port_offset), "--force"]
            ],
        ),
        Gate("smoke-nvflare", [Check(verified), ["make", "e2e_smoke", "FL_BACKEND=nvflare", *args]], smoke_metrics),
        Gate("switch-flower", [["make", "restart-fl", "FL_BACKEND=flower", *args], Check(verified)]),
        Gate("smoke-flower", [smoke_flower], smoke_metrics),
        Gate("integration", [["make", "integration_test", *args]]),
        Gate(
            "teardown",
            [
                ["make", "down", "FL_BACKEND=flower", *args],
                ["make", "down", "FL_BACKEND=nvflare", *args],
                ["make", "remove-networks", f"FLIP_INSTANCE={instance}"],
                Check(nothing_left),
            ],
        ),
    ]
    return {g.name: g for g in gates}


def run(
    gates: dict[str, Gate],
    selected: list[str],
    out_dir: Path,
    base: dict,
    env: dict[str, str] | Callable[[], dict[str, str]],
    keep_going: bool,
    instance: str = "",
) -> dict[str, dict]:
    """Run the selected gates in order; after a failure the rest are not run (bar teardown) unless ``keep_going``."""
    results: dict[str, dict] = {}
    failed = False
    try:
        for name in selected:
            if name == "teardown":
                continue
            if failed and not keep_going:
                results[name] = skip_gate(name, out_dir, base)
                continue
            results[name] = run_gate(gates[name], out_dir, base, env() if callable(env) else env)
            if results[name]["status"] == "failed":
                failed = True
                if instance and name in STACK_GATES:
                    saved = collect_logs(instance, out_dir / "container-logs" / name)
                    print(f"🗂  saved {len(saved)} container log(s) → {out_dir / 'container-logs' / name}", flush=True)
    finally:
        if "teardown" in selected:
            results["teardown"] = run_gate(gates["teardown"], out_dir, base, env() if callable(env) else env)
    return results


def _gpu() -> str:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True, check=True
        )
        return ", ".join(sorted(set(out.stdout.split("\n")) - {""}))
    except (OSError, subprocess.CalledProcessError):
        return ""


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", required=True)
    parser.add_argument("--instance", default="")
    parser.add_argument("--pr", default="")
    parser.add_argument("--port-offset", type=int, default=10000)
    parser.add_argument("--gates", default="", help=f"comma-separated subset of: {', '.join(ORDER)}")
    parser.add_argument("--continue-on-failure", action="store_true")
    parser.add_argument("--keep", action="store_true", help="leave the instance up (skip teardown)")
    a = parser.parse_args(argv)
    try:
        instance = a.instance or default_instance(a.tag)
        assert_clean()
        sha = assert_at_pr_head(a.pr or None)
        wanted = [g.strip() for g in a.gates.split(",") if g.strip()] or list(ORDER)
        unknown = sorted(set(wanted) - set(ORDER))
        if unknown:
            raise ReleaseError(f"unknown gate(s) {', '.join(unknown)}; choose from {', '.join(ORDER)}")
        selected = [g for g in ORDER if g in wanted and not (a.keep and g == "teardown")]
        # The instance gate rewrites every dev kit; one it did not write belongs to a developer's own trusts.
        foreign = instance_mod.foreign_kits(instance)
        if foreign:
            names = ", ".join(str(p.relative_to(REPO)) for p in foreign)
            raise ReleaseError(
                f"{names} belong to another stack, and {instance} would overwrite them along with"
                f" {env_file_name(instance)}; run the gates from a dedicated release worktree"
            )
    except (ReleaseError, subprocess.CalledProcessError) as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1

    out_dir = gates_dir(a.tag)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = {"tag": a.tag, "instance": instance, "sha": sha, "host": socket.gethostname(), "gpu": _gpu()}

    def project_id() -> str:
        return load_results(a.tag).get("smoke-nvflare", {}).get("metrics", {}).get("project_id", "")

    # The stack-free gates already run make with the instance's env file, so write it (no stack, no secrets shown)
    # if this is the first run; the instance gate rewrites it with fresh secrets before anything starts. Any kit
    # already there is this instance's (checked above), so forcing replaces nothing of anyone else's.
    if not (REPO / env_file_name(instance)).exists():
        instance_mod.write_files(instance, a.port_offset, force=True)

    gates = build_gates(a.tag, instance, project_id, a.port_offset)
    results = run(gates, selected, out_dir, base, lambda: instance_env(instance), a.continue_on_failure, instance)
    failed = [n for n, r in results.items() if r["status"] == "failed"]
    print(f"{'❌ failed: ' + ', '.join(failed) if failed else '✅ all selected gates passed'} — results in {out_dir}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
