#!/usr/bin/env python3
#
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

"""FLIP Kubernetes Deployment Status Checker.

This script verifies that the Kubernetes trust-side deployment is functioning correctly.

PREREQUISITES:
  - kubectl installed and configured
  - helm installed (optional, for chart version check)
  - The flip-trust chart deployed (``helm install flip-trust ...``)

WHAT IT CHECKS:
  ✓ Prerequisites (kubectl, helm)
  ✓ Namespace and Helm release status
  ✓ Pod readiness (all trust-side services)
  ✓ GPU allocation (fl-client)
  ✓ PersistentVolumeClaims
  ✓ XNAT plugins (XNAT on every running xnat-web pod loaded each one)
  ✓ HTTP endpoints (trust-api, imaging-api, data-access-api, Orthanc)
  ✓ MinIO S3 store (FL participant kits)
  ✓ System resources (disk, memory on the node)
  ✓ Container logs for recent errors

EXIT CODES:
  0 - All checks passed (warnings are acceptable)
  1 - One or more checks failed
"""

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass


# Color codes for terminal output
class Colors:
    """ANSI color codes for terminal output."""

    RED = "\033[0;31m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    BLUE = "\033[0;34m"
    NC = "\033[0m"  # No Color


@dataclass
class StatusCounters:
    """Track check results."""

    passed: int = 0
    failed: int = 0
    warnings: int = 0

    @property
    def total(self) -> int:
        """Get total checks."""
        return self.passed + self.failed + self.warnings


# Global counters
counters = StatusCounters()


def print_status(status: str, message: str) -> None:
    """Print a status message with color coding.

    Args:
        status: Status type (PASS, FAIL, INFO, WARN)
        message: Message to display
    """
    global counters

    symbols = {
        "PASS": f"{Colors.GREEN}✓ PASS{Colors.NC}",
        "FAIL": f"{Colors.RED}✗ FAIL{Colors.NC}",
        "WARN": f"{Colors.YELLOW}⚠ WARN{Colors.NC}",
        "INFO": f"{Colors.BLUE}ℹ INFO{Colors.NC}",
    }

    symbol = symbols.get(status, "")
    print(f"{symbol} - {message}")

    if status == "PASS":
        counters.passed += 1
    elif status == "FAIL":
        counters.failed += 1
    elif status == "WARN":
        counters.warnings += 1


def print_section(title: str) -> None:
    """Print a section header.

    Args:
        title: Section title
    """
    print()
    print(f"{Colors.BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{Colors.NC}")
    print(f"{Colors.BLUE}{title}{Colors.NC}")
    print(f"{Colors.BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{Colors.NC}")


def check_command(command: str) -> bool:
    """Check if a command is available.

    Args:
        command: Command name to check

    Returns:
        True if command exists, False otherwise
    """
    try:
        subprocess.run(
            ["which", command],
            capture_output=True,
            check=True,
            timeout=5,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def run_command(args: list[str], timeout: int = 30) -> tuple[bool, str]:
    """Run a shell command.

    Args:
        args: Command arguments
        timeout: Command timeout in seconds

    Returns:
        Tuple of (success, output)
    """
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout,
        )
        return True, result.stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        # A timeout carries None or bytes here even with text=True, so normalise before callers
        # treat it as text — a wedged cluster is exactly when this script must not crash. The
        # reason, never str(e): that is the whole argv, an in-pod script included.
        err = (e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else e.stderr or "").strip()
        if isinstance(e, subprocess.TimeoutExpired):
            return False, f"timed out after {e.timeout:g}s" + (f": {err}" if err else "")
        return False, err or f"exit {e.returncode}"
    except FileNotFoundError as e:
        # A tool this host lacks (macOS has no `free`) is a check that cannot run, not a crash.
        return False, str(e)


def kubectl_get_condition(resource_type: str, resource_name: str, condition: str) -> bool:
    """Check a Kubernetes resource condition.

    Args:
        resource_type: e.g. 'pod', 'deployment', 'pvc'
        resource_name: Resource name
        condition: 'Ready', 'Bound', 'Available' etc.

    Returns:
        True if condition is met
    """
    try:
        result = subprocess.run(
            [
                "kubectl",
                "get",
                resource_type,
                resource_name,
                "-o",
                f"jsonpath='{{.status.conditions[?(@.type==\"{condition}\")].status}}'",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return result.stdout.strip().strip("'") == "True"
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def kubectl_get(items: list[str], namespace: str, timeout: int = 15) -> tuple[bool, str]:
    """Run a kubectl get command and return the output.

    Args:
        items: What to get (e.g. ['pods', '-l', 'app=foo'])
        namespace: Kubernetes namespace
        timeout: Command timeout

    Returns:
        Tuple of (success, output)
    """
    args = ["kubectl", "get"] + items
    if namespace:
        args.extend(["-n", namespace])
    return run_command(args, timeout=timeout)


def kubectl_json(resource: str, namespace: str, timeout: int = 15) -> dict | None:
    """Get a Kubernetes resource as JSON.

    Args:
        resource: Resource identifier (e.g. 'deployment/flip-trust-trust-api')
        namespace: Kubernetes namespace
        timeout: Command timeout

    Returns:
        Parsed JSON dict or None
    """
    args = ["kubectl", "get", resource, "-n", namespace, "-o", "json"]
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout,
        )
        return json.loads(result.stdout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def kubectl_list(items: list[str], namespace: str, timeout: int = 15) -> list[str]:
    """List Kubernetes resources by name.

    Args:
        items: What to list with -o name (e.g. ['pods'])
        namespace: Kubernetes namespace
        timeout: Command timeout

    Returns:
        List of resource names (e.g. ['pod/foo-xxx', 'pod/bar-yyy'])
    """
    args = ["kubectl", "get"] + items + ["-o", "name"]
    if namespace:
        args.extend(["-n", namespace])
    success, output = run_command(args, timeout=timeout)
    if not success:
        return []
    return [line.strip() for line in output.split("\n") if line.strip()]


def _fl_client_kit_missing(pod: str, namespace: str) -> bool:
    """Report whether an unready fl-client pod is blocked on its un-staged FL kit.

    The kit is a hostPath volume with `type: Directory` (templates/fl-client.yaml), so
    an unstaged kit surfaces as a pod stuck outside Running with a kubelet event naming
    the path — not as an application error. Matching on the event keeps this specific:
    a genuinely broken fl-client still reports FAIL.

    Args:
        pod: Pod resource name as returned by kubectl_list (e.g. 'pod/fl-client-xxx')
        namespace: Kubernetes namespace.

    Returns:
        True if the pod is not Running and a mount event names a hostPath failure.
    """
    success, phase = run_command(["kubectl", "get", pod, "-n", namespace, "-o", "jsonpath={.status.phase}"])
    if not success or phase.strip().strip("'\"") == "Running":
        return False
    # Events carry the diagnosis; the pod status only says ContainerCreating.
    success, events = run_command(
        [
            "kubectl",
            "get",
            "events",
            "-n",
            namespace,
            "--field-selector",
            f"involvedObject.name={pod.split('/')[-1]}",
            "-o",
            "jsonpath={.items[*].message}",
        ]
    )
    return success and "hostPath type check failed" in events


# The id GET /xapi/plugins lists each plugin under — the `id=` in the jar's
# META-INF/xnat/*-plugin.properties, read from the jars and the API on XNAT 1.10.0. The image is
# built from trust/xnat/artifacts.manifest; tests/test_plugin_roster_and_timeout.py keeps the two
# in step.
XNAT_PLUGIN_IDS = {
    "batch-launch": "batchLaunchPlugin",
    "container-service": "containers",
    "dicom-query-retrieve": "dicom-query-retrieve",
    "ohif-viewer": "ohifViewerPlugin",
}

# Runs in the xnat-web pod as the FLIP service account. The credential is the pod's own
# (XNAT_SERVICE_USER/PASSWORD from the release Secret) and reaches curl on stdin (`-K -`), so it is
# in no argv here or in the pod. Prints the plugin list's body and HTTP status, then the DQR
# settings route's status, each on its own line. XNAT lists a plugin whose configuration failed to
# load, so only the route shows the plugins registered (wait-for-xnat-plugins.sh).
_FETCH_LOADED_PLUGINS = r"""
[ -n "${XNAT_SERVICE_USER:-}" ] && [ -n "${XNAT_SERVICE_PASSWORD:-}" ] || {
  echo "XNAT_SERVICE_USER / XNAT_SERVICE_PASSWORD are not set in this container" >&2; exit 3; }
token=$(printf '%s:%s' "$XNAT_SERVICE_USER" "$XNAT_SERVICE_PASSWORD" | base64 | tr -d '\n')
ask() { printf 'header = "Authorization: Basic %s"\n' "$token" \
  | curl -sS --connect-timeout 5 --max-time 20 -K - "$@"; }
ask -w '\n%{http_code}\n' http://localhost:8080/xapi/plugins
ask -o /dev/null -w '%{http_code}' http://localhost:8080/xapi/dqr/settings
"""


@dataclass(frozen=True)
class XnatAnswer:
    """What one pod's XNAT answered.

    Attributes:
        plugins: ``{plugin id: version}`` from GET /xapi/plugins. The version is XNAT's own: the
            plugin's declared one, else empty.
        dqr_status: The HTTP status GET /xapi/dqr/settings answered.
        problem: Why the plugin list is unusable; empty when it is usable.
        answered: Whether XNAT replied at all. A wrong reply is a FAIL, no reply a WARN.
    """

    plugins: dict[str, str] | None = None
    dqr_status: str = ""
    problem: str = ""
    answered: bool = True


def ask_xnat_plugins(pod: str, namespace: str) -> XnatAnswer:
    """Ask a pod's XNAT which plugins it lists, and whether its DQR route answers.

    Args:
        pod: Pod resource name.
        namespace: Kubernetes namespace.

    Returns:
        An :class:`XnatAnswer`.
    """
    success, output = run_command(
        ["kubectl", "exec", pod, "-n", namespace, "--", "sh", "-c", _FETCH_LOADED_PLUGINS], timeout=45
    )
    if not success:
        # kubectl names the defaulted container on every exec into xnat-web; that is not the error.
        lines = [line for line in output.splitlines() if line.strip() and not line.startswith("Defaulted container")]
        return XnatAnswer(problem=" ".join(lines)[:200], answered=False)
    head, _, dqr_status = output.rpartition("\n")
    body, _, status = head.rpartition("\n")
    status = status.strip()
    if status in ("", "000"):
        return XnatAnswer(problem="GET /xapi/plugins got no HTTP answer", answered=False)
    if status != "200":
        return XnatAnswer(problem=f"GET /xapi/plugins answered HTTP {status}")
    try:
        listing = json.loads(body)
    except json.JSONDecodeError:
        listing = None
    if not isinstance(listing, dict):
        return XnatAnswer(problem="GET /xapi/plugins did not answer a JSON object")
    plugins = {pid: (bean.get("version") or "") if isinstance(bean, dict) else "" for pid, bean in listing.items()}
    return XnatAnswer(plugins=plugins, dqr_status=dqr_status.strip())


def xnat_enabled(helm_release: str, namespace: str) -> tuple[bool | None, str]:
    """Ask the live release whether it runs XNAT (``helm get values --all``).

    Args:
        helm_release: Helm release name.
        namespace: Kubernetes namespace.

    Returns:
        ``(enabled, "")``, or ``(None, why not)`` when the values cannot tell. That stays distinct
        from "off", which skips the check: values that cannot be read warn, and the pods are still
        checked.
    """
    if not check_command("helm"):
        return None, "helm is not on PATH"
    success, output = run_command(
        ["helm", "get", "values", helm_release, "-n", namespace, "--all", "-o", "json"],
        timeout=30,
    )
    if not success:
        return None, f"`helm get values {helm_release} -n {namespace}` failed: {output.strip()[:200]}"
    try:
        values = json.loads(output)
    except json.JSONDecodeError as exc:
        return None, f"helm's values are not valid JSON: {exc}"
    xnat = values.get("xnat") if isinstance(values, dict) else None
    enabled = xnat.get("enabled") if isinstance(xnat, dict) else None
    if not isinstance(enabled, bool):
        return None, "the release's values carry no xnat.enabled"
    return enabled, ""


def check_xnat_plugins(helm_release: str, namespace: str) -> None:
    """Assert XNAT on every running xnat-web pod lists every plugin and answers its DQR route.

    XNAT that answers wrongly, or lists a plugin short, FAILs; a pod XNAT cannot be asked on WARNs.
    A plugin that loads can still abort every C-STORE at run time (a manifest pairing it with the
    wrong XNAT core), which ``make smoke-cstore`` covers.

    Args:
        helm_release: Helm release name.
        namespace: Kubernetes namespace.
    """
    enabled, why = xnat_enabled(helm_release, namespace)
    if enabled is False:
        print_status("INFO", "Skipping the XNAT plugin check (xnat.enabled is false in this release)")
        return
    if enabled is None:
        print_status("WARN", f"Cannot tell whether this release runs XNAT ({why}); checking any xnat-web pod anyway")

    pods = kubectl_list(
        [
            "pods",
            "-l",
            f"app.kubernetes.io/instance={helm_release},app.kubernetes.io/component=xnat-web",
            "--field-selector=status.phase=Running",
        ],
        namespace,
    )
    if not pods:
        pods = kubectl_list(
            ["pods", "-l", "app.kubernetes.io/component=xnat-web", "--field-selector=status.phase=Running"],
            namespace,
        )
    if not pods:
        print_status("WARN", "No running xnat-web pod — cannot verify its plugins")
        return

    # EVERY Running pod, not pods[0]. Running is not Ready: during a rollout — and
    # indefinitely if one stalls — a new pod sits Running beside the old one while the
    # Service still routes to the old. Checking whichever sorted first could PASS the new
    # pod while every C-STORE still lands on the old one.
    if len(pods) > 1:
        print_status(
            "WARN",
            f"{len(pods)} xnat-web pods are Running ({', '.join(pods)}) — a rollout is in progress or "
            "stalled. Each is checked below, but note the Service may still be routing stores to the "
            "OLD pod, so a clean result on the new one does not mean stores are landing on it.",
        )

    for pod in pods:
        answer = ask_xnat_plugins(pod, namespace)
        if answer.plugins is None:
            if answer.answered:
                print_status("FAIL", f"XNAT on '{pod}': {answer.problem}")
            else:
                print_status("WARN", f"Could not ask XNAT on '{pod}' which plugins it loaded ({answer.problem})")
            continue
        unloaded = [key for key, plugin_id in sorted(XNAT_PLUGIN_IDS.items()) if plugin_id not in answer.plugins]
        for key in unloaded:
            print_status(
                "FAIL",
                f"XNAT on '{pod}' did not load the {key} plugin (not in this image, or it failed at startup: "
                "see the tomcat log)",
            )
        # 404 is an unregistered route; any other answer means it exists (the service account may
        # lack DQR admin rights).
        if answer.dqr_status == "404":
            print_status(
                "FAIL",
                f"XNAT on '{pod}' has no DQR route (GET /xapi/dqr/settings answered 404): a plugin's "
                "configuration failed to load, see the tomcat log",
            )
        elif answer.dqr_status in ("", "000"):
            print_status("WARN", f"Could not reach XNAT's DQR route on '{pod}' (no HTTP answer)")
        elif not unloaded:
            versions = ", ".join(
                f"{pid} {answer.plugins[pid] or '(no version)'}" for pid in sorted(XNAT_PLUGIN_IDS.values())
            )
            print_status(
                "PASS",
                f"XNAT on '{pod}' lists all {len(XNAT_PLUGIN_IDS)} plugins and its DQR route answers: {versions}",
            )


def check_http_endpoint(url: str, name: str, expected_status: int | list[int] = 200) -> bool:
    """Check HTTP endpoint availability.

    Args:
        url: URL to check
        name: Endpoint name for logging
        expected_status: Expected HTTP status code(s). Can be a single int or list of ints.

    Returns:
        True if endpoint is accessible, False otherwise
    """
    print_status("INFO", f"Checking {name} at {url}...")

    valid_statuses = [expected_status] if isinstance(expected_status, int) else expected_status

    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status in valid_statuses:
                print_status("PASS", f"{name} is responding (HTTP {response.status})")
                return True
            else:
                print_status(
                    "FAIL",
                    f"{name} returned HTTP {response.status} (expected {valid_statuses})",
                )
                return False
    except urllib.error.HTTPError as e:
        if e.code in valid_statuses:
            print_status("PASS", f"{name} is responding (HTTP {e.code})")
            return True
        else:
            print_status("FAIL", f"{name} returned HTTP {e.code} (expected {valid_statuses})")
            return False
    except Exception as e:
        print_status("FAIL", f"{name} not responding: {e}")
        return False


def forward_then_check(
    pod_name: str,
    remote_port: int,
    url: str,
    name: str,
    expected: int | list[int],
    namespace: str,
    timeout: int = 20,
) -> bool:
    """Port-forward to a pod, check the endpoint, then clean up.

    This is a diagnostic convenience — not a persistent tunnel. Each check
    starts and stops its own port-forward.

    Args:
        pod_name: Pod name (e.g. 'deploy/flip-trust-trust-api')
        remote_port: Container port to forward
        url: URL to probe on the forwarded port
        name: Check name for logging
        expected: Expected HTTP status code(s)
        namespace: Kubernetes namespace the pod lives in
        timeout: How long to wait for the forward to establish

    Returns:
        True if endpoint responds as expected
    """
    import socket

    # Find a free local port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        local_port = s.getsockname()[1]

    proc = subprocess.Popen(
        ["kubectl", "port-forward", "-n", namespace, pod_name, f"{local_port}:{remote_port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for port-forward to become ready
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", local_port), timeout=1):
                break
        except (ConnectionRefusedError, OSError):
            time.sleep(0.5)
    else:
        _, stderr = proc.communicate(timeout=2)
        proc.terminate()
        proc.wait()
        err_msg = stderr.decode() if stderr else "no output"
        print_status("FAIL", f"Port-forward to {pod_name}:{remote_port} did not become ready: {err_msg[:120]}")
        return False

    local_url = url.replace(f":{remote_port}", f":{local_port}").replace("localhost", "127.0.0.1")

    result = check_http_endpoint(local_url, name, expected)

    proc.terminate()
    proc.wait()
    return result


def main(
    namespace: str,
    helm_release: str,
    skip_endpoints: bool,
    skip_gpu: bool,
) -> None:
    """Main entry point for Kubernetes smoke tests.

    Args:
        namespace: Kubernetes namespace for the flip-trust release
        helm_release: Helm release name
        skip_endpoints: Skip port-forward + HTTP checks
        skip_gpu: Skip GPU allocation checks
    """
    # ── Prerequisites ────────────────────────────────────────────────────
    print_section("Checking Prerequisites")

    if not check_command("kubectl"):
        print_status("FAIL", "Required command 'kubectl' not found. Please install it.")
        sys.exit(1)
    print_status("PASS", "kubectl command found")

    if not check_command("helm"):
        print_status("WARN", "helm not found — Helm release checks will be limited")
    else:
        print_status("PASS", "helm command found")

    # ── K8s cluster connectivity ─────────────────────────────────────────
    print_section("Kubernetes Cluster Connectivity")

    success, nodes = run_command(["kubectl", "get", "nodes", "-o", "name"])
    if not success or not nodes.strip():
        print_status("FAIL", "Cannot connect to Kubernetes cluster — check kubeconfig and context")
        sys.exit(1)

    node_names = [n.replace("node/", "") for n in nodes.strip().split("\n")]
    print_status("PASS", f"Connected — {len(node_names)} node(s): {', '.join(node_names)}")

    # ── Namespace ─────────────────────────────────────────────────────────
    print_section("Namespace and Helm Release")

    success, ns_output = run_command(["kubectl", "get", "ns", namespace])
    if not success or "Active" not in ns_output:
        print_status("FAIL", f"Namespace '{namespace}' not found. Deploy first with: helm install {helm_release} ...")
        sys.exit(1)
    print_status("PASS", f"Namespace '{namespace}' exists")

    if check_command("helm"):
        # Helm 4 removed --all and exits 1 when any release is in a failed state,
        # even though it still emits valid JSON on stdout.  Capture stdout directly
        # so we get the release list regardless of the exit code.
        try:
            result = subprocess.run(
                ["helm", "list", "-n", namespace, "-o", "json"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            hr = result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            hr = ""
        if hr and hr != "[]":
            try:
                releases = json.loads(hr)
                match = [r for r in releases if r.get("name") == helm_release]
                if match:
                    rel = match[0]
                    print_status(
                        "PASS",
                        f"Helm release '{helm_release}' is {rel.get('status', '?')} "
                        f"(revision {rel.get('revision', '?')})",
                    )
                else:
                    print_status("FAIL", f"Helm release '{helm_release}' not found in namespace '{namespace}'")
            except json.JSONDecodeError:
                print_status("WARN", "Could not parse Helm release list")
        else:
            print_status("FAIL", f"No Helm releases in namespace '{namespace}'")
    else:
        print_status("INFO", "Skipping Helm release check (helm not available)")

    # ── Pod readiness ─────────────────────────────────────────────────────
    print_section("Pod Readiness")

    # Expected services and their minimum replica count.
    # Discovered via label selector: app.kubernetes.io/instance=<release>,app.kubernetes.io/component=<svc>
    expected_services = {
        "trust-api": 1,
        "data-access-api": 1,
        "imaging-api": 1,
        "omop-db": 1,
        "orthanc": 1,
        "fl-client": 1,
        "xnat-web": 1,
        "xnat-nginx": 1,
        "xnat-db": 1,
        "minio": 0,  # Optional — warn if missing, pass if present
    }

    for svc, min_ready in expected_services.items():
        # Try exact instance+component label selector first, then fall back to
        # component-only for pods that may be missing the instance label.
        label_sel = f"app.kubernetes.io/instance={helm_release},app.kubernetes.io/component={svc}"
        svc_pods = kubectl_list(["pods", "-l", label_sel], namespace)
        if not svc_pods:
            # Fallback: some pods lack the instance label
            label_sel = f"app.kubernetes.io/component={svc}"
            svc_pods = kubectl_list(["pods", "-l", label_sel], namespace)
        if not svc_pods:
            if min_ready == 0:
                print_status("INFO", f"No '{svc}' pods (optional)")
            else:
                print_status("FAIL", f"No pods for '{svc}' — service may not be deployed or enabled")
            continue

        ready = 0
        for pod_name in svc_pods:
            success, status = run_command(
                ["kubectl", "get", pod_name, "-n", namespace, "-o", "jsonpath={.status.containerStatuses[0].ready}"]
            )
            if success and status.strip().strip("'\"").lower() == "true":
                ready += 1

        if ready >= min_ready:
            print_status("PASS", f"'{svc}': {ready}/{len(svc_pods)} ready")
        else:
            # For fl-client, the overwhelmingly common cause is that the FL participant
            # kit was never staged onto the node. The chart mounts it as a hostPath with
            # `type: Directory` (templates/fl-client.yaml), so a missing path is a mount
            # failure naming that path, not a broken workload — report the remediation
            # instead of a bare FAIL. (The chart holds no AWS credentials and no init
            # container: it cannot fetch the kit, which arrives out-of-band.)
            if svc == "fl-client" and _fl_client_kit_missing(svc_pods[0], namespace):
                print_status(
                    "INFO",
                    "fl-client cannot mount its FL participant kit — the path in "
                    "flClient.kitHostPath does not exist on the node. Stage it first: "
                    "make stage-kit KIT_SRC=<kit dir> KUBE_CONTEXT=<context>",
                )
            else:
                print_status("FAIL", f"'{svc}': {ready}/{len(svc_pods)} ready (expected at least {min_ready})")

    # Check for Pods in CrashLoopBackOff / Error or high restart counts
    all_pods = kubectl_list(["pods"], namespace)
    print_status("INFO", "Scanning for unhealthy pods...")
    unhealthy = []
    for pod_name in all_pods:
        success, reason = run_command(
            [
                "kubectl",
                "get",
                pod_name,
                "-n",
                namespace,
                "-o",
                "jsonpath={.status.containerStatuses[0].state.waiting.reason}",
            ]
        )
        if success and reason.strip().strip("'\"") in ("CrashLoopBackOff", "Error", "ImagePullBackOff", "ErrImagePull"):
            unhealthy.append((pod_name, reason.strip().strip("'\"")))
            continue
        # Also flag pods with excessive restarts (even if currently "running")
        success, restarts = run_command(
            ["kubectl", "get", pod_name, "-n", namespace, "-o", "jsonpath={.status.containerStatuses[0].restartCount}"]
        )
        if success:
            try:
                rc = int(restarts.strip().strip("'\""))
            except (ValueError, TypeError):
                rc = 0
            if rc >= 5:
                unhealthy.append((pod_name, f"restarted {rc} times"))

    if unhealthy:
        for pod_name, reason in unhealthy:
            print_status("FAIL", f"Unhealthy pod: {pod_name} ({reason})")
    else:
        print_status("PASS", "No pods in CrashLoopBackOff or Error state")

    # ── GPU ───────────────────────────────────────────────────────────────
    if not skip_gpu:
        print_section("GPU Allocation")

        fl_label = f"app.kubernetes.io/instance={helm_release},app.kubernetes.io/component=fl-client"
        fl_pods = kubectl_list(["pods", "-l", fl_label], namespace)
        if fl_pods:
            gpu_count = "0"
            for pod_name in fl_pods:
                field = "jsonpath={.spec.containers[0].env[?(@.name=='NUM_AVAILABLE_GPUS')].value}"
                success, value = run_command(["kubectl", "get", pod_name, "-n", namespace, "-o", field])
                if success:
                    gpu_count = value.strip().strip("'\"")
                    break

            if gpu_count and int(gpu_count) > 0:
                print_status("PASS", f"fl-client has {gpu_count} GPU(s) allocated (NUM_AVAILABLE_GPUS={gpu_count})")
            else:
                print_status(
                    "WARN", "fl-client has no GPUs allocated — check nvidia-device-plugin and time-slicing config"
                )
        else:
            print_status("INFO", "No fl-client pods — skipping GPU check")

        # Node-level allocatable GPUs
        success, gpu_alloc = run_command(
            ["kubectl", "get", "nodes", "-o", "jsonpath={.items[0].status.allocatable.nvidia\\.com/gpu}"]
        )
        if success and gpu_alloc.strip().strip("'\"") and int(gpu_alloc.strip().strip("'\"")) > 0:
            node_gpu_count = gpu_alloc.strip().strip("'\"")
            print_status("PASS", f"Node reports {node_gpu_count} allocatable GPU(s)")

    # ── PersistentVolumeClaims ────────────────────────────────────────────
    print_section("PersistentVolumeClaims")

    pvcs = kubectl_list(["pvc"], namespace)
    if not pvcs:
        print_status("INFO", "No PVCs in namespace")
    else:
        for pvc_name in pvcs:
            pvc_short = pvc_name.replace("persistentvolumeclaim/", "")
            success, phase = run_command(
                ["kubectl", "get", pvc_name, "-n", namespace, "-o", "jsonpath={.status.phase}"]
            )
            phase_str = phase.strip().strip("'\"") if success else "Unknown"
            if phase_str == "Bound":
                print_status("PASS", f"PVC '{pvc_short}' is {phase_str}")
            elif phase_str == "Pending":
                print_status(
                    "WARN", f"PVC '{pvc_short}' is {phase_str} — may be waiting for first consumer or provisioner"
                )
            else:
                print_status("FAIL", f"PVC '{pvc_short}' status: {phase_str}")

    # ── Init Jobs ─────────────────────────────────────────────────────────
    print_section("Init Jobs")

    init_jobs = [
        ("trust-release-flip-trust-omop-db-init", "omop-db"),
        ("trust-release-flip-trust-orthanc-init", "orthanc"),
        ("trust-release-flip-trust-xnat-init", "xnat"),
    ]

    for job_name, label in init_jobs:
        success, status_json = run_command(
            ["kubectl", "get", "job", job_name, "-n", namespace, "-o", "json"], timeout=10
        )
        if not success:
            print_status("INFO", f"Init job '{job_name}' not found (may not be configured)")
            continue

        try:
            job = json.loads(status_json)
        except json.JSONDecodeError:
            print_status("WARN", f"Could not parse job status for '{job_name}'")
            continue

        conditions = job.get("status", {}).get("conditions", [])
        complete = any(c.get("type") == "Complete" and c.get("status") == "True" for c in conditions)
        failed = any(c.get("type") == "Failed" and c.get("status") == "True" for c in conditions)

        if complete:
            print_status("PASS", f"Init job '{job_name}' ({label}) completed successfully")
        elif failed:
            print_status("FAIL", f"Init job '{job_name}' ({label}) failed — check its logs")
        else:
            print_status("WARN", f"Init job '{job_name}' ({label}) has not completed yet (still running?)")

    # ── XNAT plugins ──────────────────────────────────────────────────────
    print_section("XNAT Plugins")

    check_xnat_plugins(helm_release, namespace)

    # ── HTTP endpoint checks (via port-forward) ───────────────────────────
    if not skip_endpoints:
        print_section("Service Endpoint Checks")

        # Use service resources for port-forwarding (more stable than deployment refs)
        service_endpoints = [
            ("service/trust-api", 8000, "/health", "trust-api", 200),
            ("service/imaging-api", 8000, "/health", "imaging-api", 200),
            ("service/data-access-api", 8000, "/health", "data-access-api", 200),
            # Auth is always enforced (FLIP-PT-091): a 200 without credentials
            # means an unauthenticated PACS — fail.
            ("service/orthanc", 8042, "/", "Orthanc", [401]),
        ]

        for service_ref, container_port, path, name, expected in service_endpoints:
            url = f"http://localhost:{container_port}{path}"
            forward_then_check(service_ref, container_port, url, name, expected, namespace)

        # ── MinIO S3 check ────────────────────────────────────────────────
        print_section("MinIO S3 Store")

        minio_label = f"app.kubernetes.io/instance={helm_release},app.kubernetes.io/component=minio"
        minio_pods = kubectl_list(["pods", "-l", minio_label], namespace)
        if not minio_pods:
            print_status("INFO", "No MinIO pod — FL client kits may not be available without S3 sync")
        else:
            print_status("PASS", "MinIO pod found")

            # Check the fl-kits bucket exists and has the Trust_1 kit
            print_status("INFO", "Checking MinIO bucket 'fl-kits' via S3 API...")

            try:
                import boto3  # noqa: F811
                from botocore.client import Config  # noqa: F811
            except ImportError:
                print_status("WARN", "boto3 not available — skipping MinIO S3 probe. Install with: pip install boto3")
                minio_skipped = True
            else:
                minio_skipped = False

            # Port-forward then probe MinIO
            import socket

            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", 0))
                minio_local = s.getsockname()[1]

            minio_proc = subprocess.Popen(
                ["kubectl", "port-forward", "service/minio", f"{minio_local}:9000", "-n", namespace],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(2)

            if minio_skipped:
                minio_proc.terminate()
                minio_proc.wait()
            else:
                try:
                    s3 = boto3.client(
                        "s3",
                        endpoint_url=f"http://127.0.0.1:{minio_local}",
                        aws_access_key_id="minioadmin",
                        aws_secret_access_key="minioadmin",  # pragma: allowlist secret
                        config=Config(signature_version="s3v4"),
                        region_name="us-east-1",
                    )
                    buckets = [b["Name"] for b in s3.list_buckets()["Buckets"]]
                    if "fl-kits" in buckets:
                        print_status("PASS", "MinIO bucket 'fl-kits' exists")

                        # Check kit presence
                        response = s3.list_objects_v2(
                            Bucket="fl-kits",
                            Prefix="fl-flare-participant-kits/20260318/net-1/services/Trust_1/startup/",
                            MaxKeys=5,
                        )
                        if response.get("Contents"):
                            kit_files = [o["Key"] for o in response["Contents"]]
                            print_status("PASS", f"FLARE Trust_1 kit present ({len(kit_files)} files)")
                        else:
                            print_status("WARN", "FLARE Trust_1 kit not found in MinIO — run kit upload first")
                    else:
                        print_status("WARN", "MinIO bucket 'fl-kits' not found — create it and upload kits")
                except Exception as e:
                    print_status("WARN", f"Could not probe MinIO: {e}")
                finally:
                    minio_proc.terminate()
                    minio_proc.wait()

    # ── Container logs scan ───────────────────────────────────────────────
    print_section("Container Logs")

    critical_containers = {
        "app.kubernetes.io/component=trust-api": "trust-api",
        "app.kubernetes.io/component=data-access-api": "data-access-api",
        "app.kubernetes.io/component=imaging-api": "imaging-api",
        "app.kubernetes.io/component=orthanc": "orthanc",
        "app.kubernetes.io/component=fl-client": "fl-client",
        "app.kubernetes.io/component=xnat-web": "xnat-web",
        "app.kubernetes.io/component=xnat-nginx": "xnat-nginx",
    }

    for label_sel, svc_name in critical_containers.items():
        pods = kubectl_list(
            [
                "pods",
                "-l",
                f"app.kubernetes.io/instance={helm_release},{label_sel}",
                "--field-selector=status.phase=Running",
            ],
            namespace,
        )
        if not pods:
            # Fallback: some pods lack the instance label
            pods = kubectl_list(
                ["pods", "-l", label_sel, "--field-selector=status.phase=Running"],
                namespace,
            )
        if not pods:
            print_status("INFO", f"No running '{svc_name}' pods to check logs for")
            continue

        pod_name = pods[0]
        success, logs = run_command(["kubectl", "logs", pod_name, "-n", namespace, "--tail=50"], timeout=15)
        if not success:
            print_status("WARN", f"Could not retrieve logs for '{svc_name}'")
            continue

        error_patterns = ["ERROR", "CRITICAL", "Exception", "Traceback", "FATAL"]
        found = [p for p in error_patterns if p in logs]

        if found:
            # Downgrade to INFO when the only errors are about a missing hub URL —
            # that means CENTRAL_HUB_API_URL hasn't been configured yet (no KIT synced),
            # not a broken service.  Run 'make sync-kit KIT=<KIT> HUB=<env>' to fix.
            hub_not_configured = all(
                "Request URL is missing" in line or "hub" in line.lower()
                for line in logs.splitlines()
                if any(p in line for p in found)
            )
            if hub_not_configured:
                print_status(
                    "INFO",
                    f"'{svc_name}' cannot reach hub (CENTRAL_HUB_API_URL not set) — "
                    "run 'make sync-kit KIT=<KIT> HUB=<env>' to configure",
                )
            else:
                print_status("WARN", f"'{svc_name}' has errors in logs: {', '.join(found)}")
        else:
            print_status("PASS", f"'{svc_name}' logs look clean")

    # ── System resources ──────────────────────────────────────────────────
    print_section("System Resources")

    print_status("INFO", "Checking disk space...")
    success, disk = run_command(["df", "-h", "/"])
    if success:
        for line in disk.split("\n")[1:]:
            fields = line.split()
            if len(fields) >= 5:
                usage = fields[4].rstrip("%")
                try:
                    usage_int = int(usage)
                    if usage_int < 80:
                        print_status("PASS", f"Disk usage is {usage}% on /")
                    else:
                        print_status("WARN", f"Disk usage is {usage}% on / (consider cleanup)")
                except ValueError:
                    pass
                break

    print_status("INFO", "Checking memory usage...")
    success, mem = run_command(["free"])
    if not success:
        print_status("INFO", "Memory check skipped: `free` is not available on this host")
    else:
        for line in mem.split("\n"):
            if line.startswith("Mem:"):
                fields = line.split()
                if len(fields) >= 3:
                    try:
                        total = int(fields[1])
                        used = int(fields[2])
                        mem_pct = int((used / total) * 100)
                        if mem_pct < 90:
                            print_status(
                                "PASS", f"Memory usage is {mem_pct}% ({used // 1024} MiB / {total // 1024} MiB)"
                            )
                        else:
                            print_status("WARN", f"Memory usage is {mem_pct}% — high")
                    except (ValueError, ZeroDivisionError):
                        pass
                break

    # ── Summary ───────────────────────────────────────────────────────────
    print_section("Summary")

    print()
    print(f"{Colors.GREEN}Passed:   {counters.passed}/{counters.total}{Colors.NC}")
    print(f"{Colors.RED}Failed:   {counters.failed}/{counters.total}{Colors.NC}")
    print(f"{Colors.YELLOW}Warnings: {counters.warnings}/{counters.total}{Colors.NC}")
    print()

    if counters.failed == 0:
        print(f"{Colors.GREEN}✓ Kubernetes deployment verification completed successfully!{Colors.NC}")
        if counters.warnings > 0:
            print(
                f"{Colors.YELLOW}⚠ However, there are {counters.warnings} "
                f"warning(s) that should be reviewed.{Colors.NC}"
            )
        sys.exit(0)
    else:
        print(f"{Colors.RED}✗ Kubernetes deployment verification failed with {counters.failed} error(s).{Colors.NC}")
        print(
            f"{Colors.YELLOW}Please review the failed checks and ensure all services are properly deployed.{Colors.NC}"
        )
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FLIP Kubernetes Deployment Status Checker. "
        "Verifies that the Kubernetes trust-side deployment is functioning correctly."
    )
    parser.add_argument(
        "--namespace",
        default="flip-trust",
        help="Kubernetes namespace for the flip-trust release (default: flip-trust)",
    )
    parser.add_argument(
        "--helm-release",
        default="flip-trust",
        help="Helm release name (default: flip-trust)",
    )
    parser.add_argument(
        "--skip-endpoints",
        action="store_true",
        help="Skip port-forward + HTTP endpoint checks",
    )
    parser.add_argument(
        "--skip-gpu",
        action="store_true",
        help="Skip GPU allocation checks",
    )

    args = parser.parse_args()

    main(
        namespace=args.namespace,
        helm_release=args.helm_release,
        skip_endpoints=args.skip_endpoints,
        skip_gpu=args.skip_gpu,
    )
