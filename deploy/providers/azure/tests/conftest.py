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
"""Shared paths and helpers for the Azure deploy static tests (FLIP#1390)."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

AZURE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = AZURE_DIR.parent.parent.parent

# $AZ_FAIL names one command ("storage blob", "aks update") that fails; $AZ_ACCOUNT_ID replaces
# the subscription id `account show` answers; `group exists` is true only for $AZ_EXISTING_RG.
STUB_AZ = """#!/bin/bash
printf '%s\\n' "$*" >> "$AZ_LOG"
if [[ -n "${AZ_FAIL:-}" && "$1 $2" == "$AZ_FAIL" ]]; then echo "stub az: $1 $2 failed" >&2; exit 1; fi
case "$1 $2" in
    "account show") echo "${AZ_ACCOUNT_ID-00000000-0000-0000-0000-000000000000}" ;;
    "vm run-command") echo ok ;;
    "group exists") [[ " $* " == *" ${AZ_EXISTING_RG:-none} "* ]] && echo true || echo false ;;
esac
"""

# kubectl logs its arguments; `get jobs` answers $KUBECTL_JOBS (a JSON list), and `apply -f -` reads stdin.
STUB_KUBECTL = """#!/bin/bash
printf 'kubectl %s\\n' "$*" >> "$AZ_LOG"
case " $* " in
    *" get jobs "*) cat "${KUBECTL_JOBS:-/dev/null}" ;;
    *" apply -f - "*) cat > /dev/null ;;
esac
"""

# Every ref is on a pushed branch, as far as `make plan` can tell.
STUB_GIT = """#!/bin/bash
printf 'git %s\\n' "$*" >> "$AZ_LOG"
echo origin/stub
"""

# Terraform never runs for real here: it logs its arguments and answers any `output` with
# $TF_OUTPUT (default "stub").
STUB_TERRAFORM = """#!/bin/bash
printf 'terraform %s\\n' "$*" >> "$AZ_LOG"
echo "${TF_OUTPUT-stub}"
"""

# api.ipify.org: answers the Nth lookup with the Nth line of $IPIFY_ANSWERS, then repeats the last.
STUB_CURL = """#!/bin/bash
printf 'curl %s\\n' "$*" >> "$AZ_LOG"
n=$(grep -c '^curl ' "$AZ_LOG")
total=$(wc -l < "$IPIFY_ANSWERS")
sed -n "$(( n < total ? n : total ))p" "$IPIFY_ANSWERS"
"""


# Variables the caller's shell or an outer make could hand the run, bypassing the stubs.
_INHERITED = ("OPERATOR_IP", "AZ_SUBSCRIPTION", "SUB_ID", "MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEFILES")


def run_make(
    tmp_path: Path,
    *goals: str,
    ipify: list[str] | None = None,
    az_fail: str | None = None,
    account_id: str | None = None,
    existing_rg: str | None = None,
    kubectl_jobs: str | None = None,
    stdin: str = "",
    tf_output: str | None = None,
    makefiles: tuple[Path, ...] = (),
    **variables: str,
) -> tuple[subprocess.CompletedProcess, list[str]]:
    """Run real Makefile goals with `az` stubbed on PATH; return the result and every stubbed call.

    `terraform`, `git` and `kubectl` are always stubbed. With `ipify`, `curl` is stubbed too and answers the IP
    lookups in that order. az calls are logged bare, the others as `terraform ...`, `git ...` and
    `curl ...`. `makefiles` are read after the Makefile, for probe targets.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stubs = {"az": STUB_AZ, "terraform": STUB_TERRAFORM, "git": STUB_GIT, "kubectl": STUB_KUBECTL}
    if ipify is not None:
        stubs["curl"] = STUB_CURL
    for name, body in stubs.items():
        stub = bin_dir / name
        stub.write_text(body)
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "az.log"
    log.touch()
    answers = tmp_path / "ipify.txt"
    answers.write_text("".join(f"{a}\n" for a in ipify or []))
    env = {k: v for k, v in os.environ.items() if k not in _INHERITED}
    env |= {"PATH": f"{bin_dir}:{os.environ['PATH']}", "AZ_LOG": str(log), "IPIFY_ANSWERS": str(answers)}
    if az_fail is not None:
        env["AZ_FAIL"] = az_fail
    if account_id is not None:
        env["AZ_ACCOUNT_ID"] = account_id
    if existing_rg is not None:
        env["AZ_EXISTING_RG"] = existing_rg
    if kubectl_jobs is not None:
        (tmp_path / "jobs.json").write_text(kubectl_jobs)
        env["KUBECTL_JOBS"] = str(tmp_path / "jobs.json")
    if tf_output is not None:
        env["TF_OUTPUT"] = tf_output
    files = [arg for f in ("Makefile", *map(str, makefiles)) for arg in ("-f", f)] if makefiles else []
    args = [f"{k}={v}" for k, v in {"AZ_SUBSCRIPTION": "trial", **variables}.items()]
    result = subprocess.run(
        ["make", "-s", "-C", str(AZURE_DIR), *files, *goals, *args],
        env=env,
        cwd=tmp_path,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result, log.read_text().splitlines()
