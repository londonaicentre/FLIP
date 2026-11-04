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

"""A non-zero `make deploy` has to mean one thing (FLIP#1645).

`deploy-trust-k8s` used to shrug at a failed deploy — "often a non-fatal post-install hook
(e.g. xnat-init — see #565), but it may be a genuine Helm failure" — and re-exit the raw code.
A human reads the output and decides; the auto-upgrade agent (FLIP#1640) cannot.

The ambiguity is removed at the source rather than classified afterwards. `xnat-init` is the hook
that makes a deploy ambiguous, so the Makefile takes it out of Helm's path (`--set
xnat.initJob.enabled=false`) and runs the existing `xnat-init` target after. The rollout is then
waited for by `scripts/rollout_wait.py`, which delegates to `kubectl rollout status` — `helm --wait`
is all-or-nothing and would fail the whole deploy on a workload that is legitimately Pending (an
`flClient` on a scale-from-zero GPU pool, or one deployed before its kit is staged). `kubectl
rollout status` applies Helm's own readiness rules (`observedGeneration` vs `generation`, the new
ReplicaSet for a Deployment, `currentRevision == updateRevision` for a StatefulSet) to the core
workloads only. Each exit code then means one thing by construction, with nothing re-deriving it
from cluster evidence:

* 1 — helm failed: the apply was rejected or a remaining hook (`omop-vocab-load`, `trust-seed`) failed;
  or the core workloads did not roll out within `HELM_TIMEOUT`;
* 3 — the release is live and rolled out, and only XNAT's configuration step failed;
* 0 — applied, rolled out, every hook succeeded.

The hook annotation stays the chart default, so a plain `helm install` still gets `xnat-init`;
only the Makefile path turns it off.

These tests read `make -n` output, so they need no cluster. `failing_hooks.py` is diagnostics on
the failure path and is tested directly.
"""

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

CHART_DIR = Path(__file__).resolve().parent.parent
HOOKS_PY = CHART_DIR / "scripts" / "failing_hooks.py"

_spec = importlib.util.spec_from_file_location("failing_hooks", HOOKS_PY)
assert _spec is not None
assert _spec.loader is not None
failing_hooks = importlib.util.module_from_spec(_spec)
sys.modules["failing_hooks"] = failing_hooks
_spec.loader.exec_module(failing_hooks)

needs_make = pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")


def deploy_recipe() -> str:
    return subprocess.run(
        ["make", "-n", "-C", str(CHART_DIR), "deploy"],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    ).stdout


# ---------------------------------------------------------------- the split step


def _upgrade_invocation(recipe: str) -> str:
    """The whole `helm upgrade` command, rejoined across its backslash continuations.

    `make -n` prints the recipe as written, so the flags sit on later lines than the one
    matching "helm upgrade"; asserting against that first line alone would pass for any flag.
    """
    lines = recipe.splitlines()
    start = next(i for i, line in enumerate(lines) if "helm upgrade --install" in line)
    collected = []
    for line in lines[start:]:
        collected.append(line.rstrip("\\").strip())
        if not line.rstrip().endswith("\\"):
            break
    return " ".join(collected)


@needs_make
def test_the_rollout_is_waited_for_before_the_deploy_reports_success():
    """Without a rollout wait, exit 0 means "applied", not "rolled out".

    FLIP#1228 was a correct chart that never reached the running pod across five upgrades. The
    wait is scoped rather than Helm's own `--wait`, because `--wait` is all-or-nothing and one
    permanently-Pending optional pod (an idle GPU fl-client) would fail every upgrade; see
    scripts/rollout_wait.py. What matters here is that SOMETHING fails the deploy when a core
    workload does not roll out.
    """
    recipe = deploy_recipe()
    assert "rollout_wait.py" in recipe, recipe
    assert recipe.index("helm upgrade --install") < recipe.index("rollout_wait.py"), recipe


@needs_make
def test_helms_own_all_or_nothing_wait_is_not_used():
    """`--wait` fails the whole upgrade on one unschedulable pod (verified on a live cluster:
    exit 1, release left `failed`, every other workload healthy). Two supported configurations
    sit Pending by design — an fl-client on a scale-from-zero GPU pool, and one awaiting its
    kit — so `--wait` would turn every unattended upgrade (FLIP#1640) into a failure."""
    assert "--wait" not in _upgrade_invocation(deploy_recipe())


@needs_make
def test_a_failed_rollout_exits_1_not_0():
    """The scoped wait must be wired to fail the deploy, not merely print."""
    recipe = deploy_recipe()
    tail = recipe[recipe.index("rollout_wait.py") :]
    assert "exit 1" in tail, tail


@needs_make
def test_xnat_init_is_taken_out_of_helms_path_and_run_after():
    """The hook inside Helm's timeout is what makes a failure ambiguous (#565)."""
    out = deploy_recipe()
    assert "--set xnat.initJob.enabled=false" in out, out
    assert out.index("helm upgrade --install") < out.index("xnat-init"), out


@needs_make
def test_an_incomplete_xnat_init_exits_3_not_1():
    """3 is still a failure; it just says "upgraded, init incomplete" rather than "not upgraded"."""
    assert "exit 3" in deploy_recipe()


@needs_make
def test_a_failed_upgrade_names_the_hook_that_failed():
    assert "failing-hooks" in deploy_recipe()


def test_nothing_reconstructs_the_verdict_from_cluster_evidence():
    """Helm's exit code is the verdict. A second opinion derived from a revision number and pod
    counts is what fails open when the apply is rejected but the revision advanced anyway."""
    assert not (CHART_DIR / "scripts" / "deploy_verdict.py").exists()


def test_the_chart_still_ships_the_hook_for_a_plain_helm_install():
    """Only the Makefile opts out; `helm install` with no flags must still configure XNAT."""
    template = (CHART_DIR / "templates" / "xnat-init-job.yaml").read_text()
    assert '"helm.sh/hook": post-install,post-upgrade' in template
    assert "xnat.initJob.enabled" in template


# ---------------------------------------------------------------- failing_hooks.py


def hook(name, phase):
    return {"name": name, "last_run": {"phase": phase} if phase is not None else {}}


def test_every_hook_succeeded_prints_nothing():
    status = {"version": 9, "hooks": [hook("xnat-init", "Succeeded"), hook("trust-seed", "Succeeded")]}
    assert failing_hooks.render(status) == ""


def test_a_failed_hook_is_named_with_its_phase():
    status = {"version": 9, "hooks": [hook("xnat-init", "Succeeded"), hook("omop-vocab-load", "Failed")]}
    line = failing_hooks.render(status)
    assert "omop-vocab-load (Failed)" in line
    assert "xnat-init" not in line


def test_a_hook_helm_skipped_is_named_as_not_run():
    """Helm stops at the first failing hook, so the later ones have no phase for this revision.
    Silently dropping them hides half of what went wrong."""
    status = {"version": 9, "hooks": [hook("omop-vocab-load", "Failed"), hook("trust-seed", None)]}
    assert "trust-seed (did not run)" in failing_hooks.render(status)


def test_a_release_with_no_hooks_is_silent():
    assert failing_hooks.render({"version": 1}) == ""


def test_unreadable_input_is_silent_rather_than_wrong():
    """A diagnostic that cannot read the cluster must not change what the deploy reported."""
    proc = subprocess.run([sys.executable, str(HOOKS_PY)], input="not json", capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_runs_as_a_script_over_real_helm_status_shape():
    status = {"version": 4, "info": {"status": "failed"}, "hooks": [hook("trust-seed", "Failed")]}
    proc = subprocess.run(
        [sys.executable, str(HOOKS_PY)],
        input=json.dumps(status),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0
    assert "trust-seed (Failed)" in proc.stdout
    assert "revision 4" in proc.stdout
