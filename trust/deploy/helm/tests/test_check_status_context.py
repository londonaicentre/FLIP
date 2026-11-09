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

"""`check_status.py --context <ctx>` sends every kubectl and helm call to <ctx> (FLIP#1807).

The smoke tests ran bare `kubectl` / `helm`, so on a host with two clusters they judged the wrong
one: a page of FAILs for a healthy release, or a pass describing another cluster. The `make status`
wiring is tested in test_makefile.py.
"""

import ast
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check_status.py"

# Stands in for kubectl and helm: records its argv (first line only: the XNAT exec passes a
# multi-line script) and answers just enough for the script to reach its summary.
STUB = """#!/bin/sh
echo "$(basename "$0") $*" | head -1 >> "$CALLS"
case "$*" in
  *"config current-context"*) echo kind-current ;;
  *"get nodes -o name"*) echo node/n1 ;;
  *"get ns "*) echo "flip-trust Active" ;;
  *"-o name"*) echo pod/p1 ;;
  *"list -n"*) echo "[]" ;;
esac
"""

# A host with no usable cluster: kubectl writes retries to stderr first and the reason last.
BROKEN = """#!/bin/sh
echo "$(basename "$0") $*" >> "$CALLS"
case "$*" in
  *"config current-context"*) echo "error: current-context is not set" >&2 ;;
  *) echo "E1109 couldn't get current server API group list" >&2
     echo "The connection to the server was refused" >&2 ;;
esac
exit 1
"""


def _run(tmp_path, *args, stub=STUB, reaches_summary=True):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("kubectl", "helm"):
        (bin_dir / name).write_text(stub)
        (bin_dir / name).chmod(0o755)
    calls = tmp_path / "calls"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--skip-endpoints", *args],
        capture_output=True,
        text=True,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "CALLS": str(calls)},
        timeout=120,
    )
    assert ("Summary" in result.stdout) == reaches_summary, result.stdout + result.stderr
    return result.stdout, calls.read_text().splitlines()


def test_every_call_targets_the_context_it_was_given(tmp_path):
    stdout, calls = _run(tmp_path, "--context", "kind-other")

    stray = [c for c in calls if not c.startswith(("kubectl --context kind-other ", "helm --kube-context kind-other "))]
    assert not stray, stray
    assert {c.split()[0] for c in calls} == {"kubectl", "helm"}
    assert "Checking context 'kind-other'" in stdout


def test_without_a_context_kubectls_current_one_is_checked_and_named(tmp_path):
    stdout, calls = _run(tmp_path)

    assert not [c for c in calls if "context " in c.replace("config current-context", "")], calls
    assert "Checking kubectl's current context ('kind-current')" in stdout


def test_a_cluster_it_cannot_reach_fails_with_kubectls_reason(tmp_path):
    """A misspelt context, a refused API server and an expired token all used to read the same."""
    stdout, _ = _run(tmp_path, stub=BROKEN, reaches_summary=False)

    assert "Checking kubectl's current context (error: current-context is not set)" in stdout
    assert "Cannot connect to Kubernetes cluster: The connection to the server was refused" in stdout
    assert "E1109" not in stdout


def test_no_command_names_kubectl_or_helm_itself():
    """Covers the calls the run above skips (port-forwards) and the helpers nothing calls yet."""
    tree = ast.parse(SCRIPT.read_text())
    base = {
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") in ("KUBECTL", "HELM")
    }
    bare = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, (ast.List, ast.Tuple))
        and node not in base
        and any(isinstance(e, ast.Constant) and e.value in ("kubectl", "helm") for e in node.elts)
    ]

    assert len(base) == 2
    assert not bare, f"check_status.py lines {bare}: start the command from KUBECTL / HELM, which carry the context"
