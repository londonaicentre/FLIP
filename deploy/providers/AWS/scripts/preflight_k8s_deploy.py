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
"""Pre-flight checks for ``make full-deploy-with-k8s``.

Verifies the prerequisites that would otherwise surface as cryptic failures
deep in the deploy chain:

1. ``"Trust_K8s"`` appears in ``FL_KIT_SLOT_NAMES`` in the active env file —
   required so Terraform seeds the slot into the DB and ``register-trusts``
   can claim it.
2. kubectl context ``flip-k3s`` is present in the local kubeconfig.
3. The K8s cluster (flip-k3s) is reachable (``cluster-info`` passes).
4. helm ≥ 3 is installed — required for the Kubernetes chart deploy step.
5. (Info) Public IP of the deploying host — used as ``K8S_TRUST_IP`` when
   not provided explicitly; shown so the operator can verify the correct
   egress address before the NLB rule is created.

Exits 0 when every check passes (warnings are advisory), 1 otherwise.

Usage:
    uv run scripts/preflight_k8s_deploy.py [--prod stag|true] [--k8s-trust-ip <ip>]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

K8S_TRUST_CODE = "Trust_K8s"
K8S_KUBECTL_CONTEXT = "flip-k3s"
WIDTH = 71

# ─────────────────────────────────────────────────────────────────────
# Output helpers  (identical style to onboard_onprem_trust.py)
# ─────────────────────────────────────────────────────────────────────

_TTY = sys.stdout.isatty()
RESET  = "\033[0m"  if _TTY else ""
BOLD   = "\033[1m"  if _TTY else ""
DIM    = "\033[2m"  if _TTY else ""
GREEN  = "\033[32m" if _TTY else ""
RED    = "\033[31m" if _TTY else ""
YELLOW = "\033[33m" if _TTY else ""
CYAN   = "\033[36m" if _TTY else ""


class Status(Enum):
    PASS    = ("✅", GREEN)
    FAIL    = ("❌", RED)
    WARN    = ("⚠️ ", YELLOW)  # trailing space — ⚠️ renders narrow on some terminals

    @property
    def glyph(self) -> str:
        return self.value[0]

    @property
    def colour(self) -> str:
        return self.value[1]


@dataclass
class Check:
    label:  str
    status: Status
    detail: str = ""
    hints:  list[str] = field(default_factory=list)


def rule(char: str = "═") -> None:
    print(char * WIDTH)


def heading(text: str) -> None:
    rule()
    print(f"  {BOLD}{text}{RESET}")
    rule()


def render_check(c: Check, label_width: int) -> None:
    label = f"{c.label}:".ljust(label_width)
    print(f"  {c.status.colour}{c.status.glyph}{RESET} {label} {c.detail}")
    for hint in c.hints:
        print(f"       {DIM}→ {hint}{RESET}")


# ─────────────────────────────────────────────────────────────────────
# Env-file helpers
# ─────────────────────────────────────────────────────────────────────


def resolve_env_file(prod: str, repo_root: Path) -> Path:
    """Return the active env file path for the given PROD value."""
    if prod == "true":
        return repo_root / ".env.production"
    return repo_root / ".env.stag"


def read_env_vars(env_file: Path) -> dict[str, str]:
    """Parse a shell env file into a dict. Returns empty dict if absent."""
    if not env_file.is_file():
        return {}
    out: dict[str, str] = {}
    for raw in env_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


# ─────────────────────────────────────────────────────────────────────
# Individual checks
# ─────────────────────────────────────────────────────────────────────


def check_kit_slot(env_vars: dict[str, str], env_file: Path) -> Check:
    """Verify Trust_K8s is in FL_KIT_SLOT_NAMES."""
    raw = env_vars.get("FL_KIT_SLOT_NAMES", "")
    if not raw:
        return Check(
            f"{K8S_TRUST_CODE} in FL_KIT_SLOT_NAMES", Status.FAIL,
            "FL_KIT_SLOT_NAMES not set in env file",
            hints=[
                f"Add FL_KIT_SLOT_NAMES=[\"Trust_1\", \"Trust_2\", \"{K8S_TRUST_CODE}\"]",
                f"to {env_file.name}, then re-apply Terraform so the slot is seeded.",
            ],
        )
    try:
        slot_names: list[str] = json.loads(raw)
    except json.JSONDecodeError:
        return Check(
            f"{K8S_TRUST_CODE} in FL_KIT_SLOT_NAMES", Status.FAIL,
            f"cannot parse FL_KIT_SLOT_NAMES as JSON: {raw!r}",
            hints=[f"Expected a JSON array, e.g. [\"Trust_1\", \"{K8S_TRUST_CODE}\"]"],
        )
    if K8S_TRUST_CODE in slot_names:
        return Check(
            f"{K8S_TRUST_CODE} in FL_KIT_SLOT_NAMES", Status.PASS,
            f"slot present ({', '.join(slot_names)})",
        )
    return Check(
        f"{K8S_TRUST_CODE} in FL_KIT_SLOT_NAMES", Status.FAIL,
        f"missing — current slots: {slot_names}",
        hints=[
            f"Add \"{K8S_TRUST_CODE}\" to FL_KIT_SLOT_NAMES in {env_file.name}:",
            f"  FL_KIT_SLOT_NAMES={json.dumps(slot_names + [K8S_TRUST_CODE])}",
            "Then re-run `make plan apply` so the slot is seeded into the DB.",
        ],
    )


def check_kubectl_context() -> Check:
    """Verify the flip-k3s kubectl context exists."""
    try:
        result = subprocess.run(
            ["kubectl", "config", "get-contexts", K8S_KUBECTL_CONTEXT, "--no-headers"],
            capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError:
        return Check(
            f"kubectl context {K8S_KUBECTL_CONTEXT}", Status.FAIL,
            "kubectl not found in PATH",
            hints=["Install kubectl: https://kubernetes.io/docs/tasks/tools/"],
        )
    except subprocess.TimeoutExpired:
        return Check(
            f"kubectl context {K8S_KUBECTL_CONTEXT}", Status.FAIL,
            "kubectl timed out",
        )
    if result.returncode == 0 and result.stdout.strip():
        return Check(
            f"kubectl context {K8S_KUBECTL_CONTEXT}", Status.PASS,
            "context present in kubeconfig",
        )
    return Check(
        f"kubectl context {K8S_KUBECTL_CONTEXT}", Status.FAIL,
        "context not found in kubeconfig",
        hints=[
            "Install k3s and merge its kubeconfig:",
            "  curl -sfL https://get.k3s.io | K3S_KUBECONFIG_MODE=644 sh -",
            "  KUBECONFIG=~/.kube/config:/etc/rancher/k3s/k3s.yaml \\",
            "    kubectl config view --flatten > /tmp/merged.yaml && mv /tmp/merged.yaml ~/.kube/config",
            f"  kubectl config rename-context default {K8S_KUBECTL_CONTEXT}",
        ],
    )


def check_cluster_reachable() -> Check:
    """Verify the flip-k3s cluster is accessible."""
    try:
        result = subprocess.run(
            ["kubectl", "cluster-info", "--context", K8S_KUBECTL_CONTEXT],
            capture_output=True, text=True, timeout=15,
        )
    except FileNotFoundError:
        return Check(
            "K8s cluster reachable", Status.FAIL,
            "kubectl not found — install kubectl first",
        )
    except subprocess.TimeoutExpired:
        return Check(
            "K8s cluster reachable", Status.FAIL,
            "cluster-info timed out (cluster unreachable)",
            hints=["Check k3s is running: systemctl status k3s"],
        )
    if result.returncode == 0:
        # Extract the control plane line for a compact display
        for line in result.stdout.splitlines():
            if "control plane" in line.lower() or "is running" in line.lower():
                return Check("K8s cluster reachable", Status.PASS, line.strip())
        return Check("K8s cluster reachable", Status.PASS, "cluster-info OK")
    return Check(
        "K8s cluster reachable", Status.FAIL,
        "cluster-info returned non-zero",
        hints=[
            "Check k3s service: systemctl status k3s",
            f"Verify kubectl context: kubectl config use-context {K8S_KUBECTL_CONTEXT}",
        ],
    )


def check_helm() -> Check:
    """Verify helm ≥ 3 is installed."""
    helm_path = shutil.which("helm")
    if not helm_path:
        return Check(
            "helm ≥ 3 installed", Status.FAIL,
            "helm not found in PATH",
            hints=[
                "macOS:  brew install helm",
                "Linux:  curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash",
                "Docs:   https://helm.sh/docs/intro/install/",
            ],
        )
    try:
        result = subprocess.run(
            ["helm", "version", "--short"],
            capture_output=True, text=True, timeout=10,
        )
        version_str = result.stdout.strip()
    except subprocess.TimeoutExpired:
        version_str = "(version check timed out)"
    # helm version --short outputs e.g. "v3.17.0+g..." or "v4.0.0+g..."
    major = 0
    if version_str.startswith("v"):
        try:
            major = int(version_str[1:].split(".")[0])
        except (ValueError, IndexError):
            pass
    if major >= 3:
        return Check("helm ≥ 3 installed", Status.PASS, version_str)
    return Check(
        "helm ≥ 3 installed", Status.FAIL,
        f"found {version_str or '(unknown version)'} — need ≥ 3",
        hints=["Upgrade helm: https://helm.sh/docs/intro/install/"],
    )


def check_public_ip(provided_ip: str | None) -> Check:
    """Show the K8S_TRUST_IP that will be used for the FL-server NLB rule.

    This is informational — a WARN when auto-detection fails so the
    operator knows they must supply the IP manually.
    """
    if provided_ip:
        return Check(
            "K8S_TRUST_IP", Status.PASS,
            f"{provided_ip} (provided explicitly)",
        )
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=5) as resp:  # noqa: S310
            detected = resp.read().decode("ascii").strip()
    except (urllib.error.URLError, TimeoutError, OSError):
        detected = None
    if detected:
        return Check(
            "K8S_TRUST_IP (auto-detect)", Status.PASS,
            f"{detected} — will be used for the FL-server NLB rule",
        )
    return Check(
        "K8S_TRUST_IP (auto-detect)", Status.WARN,
        "cannot reach api.ipify.org — IP undetectable",
        hints=[
            "Provide the K8s node's public/egress IP explicitly:",
            f"  make full-deploy-with-k8s K8S_TRUST_IP=<ip> PROD=<env>",
        ],
    )


# ─────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────


def run_checks(prod: str, k8s_trust_ip: str | None, repo_root: Path) -> list[Check]:
    env_file = resolve_env_file(prod, repo_root)
    env_vars = read_env_vars(env_file)
    return [
        check_kit_slot(env_vars, env_file),
        check_kubectl_context(),
        check_cluster_reachable(),
        check_helm(),
        check_public_ip(k8s_trust_ip),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--prod", default="stag",
        help="PROD value (stag or true). Selects .env.stag or .env.production.",
    )
    parser.add_argument(
        "--k8s-trust-ip", default=None,
        help="K8s node public IP (overrides auto-detection).",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent.parent.parent.parent

    print()
    heading(f"Pre-flight checks — full-deploy-with-k8s  (PROD={args.prod})")
    print()

    checks = run_checks(args.prod, args.k8s_trust_ip, repo_root)
    label_width = max(len(c.label) + 1 for c in checks)
    for c in checks:
        render_check(c, label_width)

    n_fail = sum(1 for c in checks if c.status == Status.FAIL)
    n_warn = sum(1 for c in checks if c.status == Status.WARN)
    n_pass = sum(1 for c in checks if c.status == Status.PASS)

    print()
    if n_fail == 0:
        warn_suffix = f", {n_warn} warning{'s' if n_warn != 1 else ''}" if n_warn else ""
        heading(f"Status: READY ✅  ({n_pass}/{len(checks)} pass{warn_suffix})")
        print()
        sys.exit(0)

    heading(f"Status: NOT READY  ({n_fail} fail, {n_pass} pass{f', {n_warn} warn' if n_warn else ''})")
    print(f"  Fix the {RED}❌{RESET} items above before deploying.")
    print()
    sys.exit(1)


if __name__ == "__main__":
    main()
