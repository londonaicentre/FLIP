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

"""A non-zero `helm upgrade` has to mean one thing (FLIP#1645).

`deploy-trust-k8s` used to shrug at a failed deploy — "often a non-fatal post-install hook
(e.g. xnat-init — see #565), but it may be a genuine Helm failure" — and re-exit the raw code.
A human reads the output and decides. The auto-upgrade agent (FLIP#1640) cannot, so the
ambiguity is resolved from cluster evidence instead of guessed at.

These tests pin the three things that make the resolution trustworthy:

* a revision that did NOT advance is a real failure — Helm applies manifests before it runs
  hooks, so an un-advanced revision means the cluster still runs the old spec;
* `Ready` alone never counts as rolled out. That is exactly what made FLIP#1228 invisible for
  five consecutive upgrades: the old pods were Ready the whole time. The controller must have
  observed the new generation, and every replica must be updated as well as ready;
* missing evidence fails closed. An unreadable release or unreadable workloads is reported as a
  failure, never as a quiet pass.

No cluster: `decide()` takes the evidence as a dict.
"""

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

CHART_DIR = Path(__file__).resolve().parent.parent
VERDICT_PY = CHART_DIR / "scripts" / "deploy_verdict.py"

_spec = importlib.util.spec_from_file_location("deploy_verdict", VERDICT_PY)
assert _spec is not None
assert _spec.loader is not None
deploy_verdict = importlib.util.module_from_spec(_spec)
sys.modules["deploy_verdict"] = deploy_verdict
_spec.loader.exec_module(deploy_verdict)

PASS = deploy_verdict.PASS
FAILED = deploy_verdict.FAILED
HOOK_FAILED = deploy_verdict.HOOK_FAILED


def workload(name="deployment/trust-api", generation=2, observed=2, desired=1, updated=1, ready=1):
    return {
        "name": name,
        "generation": generation,
        "observedGeneration": observed,
        "desired": desired,
        "updated": updated,
        "ready": ready,
    }


def evidence(**overrides):
    base = {
        "helm_rc": 1,
        "revision_before": 4,
        "revision_after": 5,
        "workloads": [workload()],
        "xnat_init_complete": True,
    }
    base.update(overrides)
    return base


class TestHelmSucceeded:
    def test_zero_exit_is_a_pass_without_consulting_the_cluster(self):
        verdict = deploy_verdict.decide({"helm_rc": 0})
        assert verdict.code == PASS
        assert verdict.reason == "helm-ok"

    def test_gather_does_not_query_workloads_when_helm_succeeded(self, monkeypatch):
        """A successful deploy must not pay for, or be able to fail on, extra cluster calls."""
        calls = []

        monkeypatch.setattr(deploy_verdict, "_json", lambda args: calls.append(args) or None)
        monkeypatch.setattr(deploy_verdict, "release_revision", lambda helm, release: 5)

        result = deploy_verdict.gather(["helm"], ["kubectl"], "flip-trust", helm_rc=0, revision_before=4)

        assert calls == []
        assert result["helm_rc"] == 0


class TestSpecNeverApplied:
    def test_unadvanced_revision_is_a_failure(self):
        """Helm applies manifests before hooks, so a static revision means nothing was deployed."""
        verdict = deploy_verdict.decide(evidence(revision_before=5, revision_after=5))
        assert verdict.code == FAILED
        assert verdict.reason == "not-applied"
        assert "still on revision 5" in verdict.detail

    def test_a_rolled_back_revision_is_a_failure(self):
        verdict = deploy_verdict.decide(evidence(revision_before=5, revision_after=4))
        assert verdict.code == FAILED
        assert verdict.reason == "not-applied"

    def test_first_install_that_created_no_revision_is_a_failure(self):
        verdict = deploy_verdict.decide(evidence(revision_before=None, revision_after=0))
        assert verdict.code == FAILED
        assert verdict.reason == "not-applied"

    def test_first_install_that_did_create_a_revision_is_judged_on_its_rollout(self):
        verdict = deploy_verdict.decide(evidence(revision_before=None, revision_after=1))
        assert verdict.code == PASS


class TestFailsClosed:
    def test_unreadable_release_is_a_failure_not_a_pass(self):
        verdict = deploy_verdict.decide(evidence(revision_after=None))
        assert verdict.code == FAILED
        assert verdict.reason == "no-release-state"

    def test_unreadable_workloads_are_a_failure_not_a_pass(self):
        verdict = deploy_verdict.decide(evidence(workloads=None))
        assert verdict.code == FAILED
        assert verdict.reason == "no-workload-state"

    def test_empty_workload_list_is_a_failure(self):
        """A release that advanced but shows no workloads is unexplained, not healthy."""
        verdict = deploy_verdict.decide(evidence(workloads=[]))
        assert verdict.code == FAILED
        assert verdict.reason == "no-workload-state"


class TestRolloutEvidence:
    def test_ready_old_pods_do_not_count_as_rolled_out(self):
        """FLIP#1228: the old pod was Ready through five upgrades that never reached the cluster."""
        stale = workload(name="deployment/xnat-web", generation=7, observed=6)
        verdict = deploy_verdict.decide(evidence(workloads=[stale]))
        assert verdict.code == FAILED
        assert verdict.reason == "rollout-incomplete"
        assert "deployment/xnat-web" in verdict.detail

    def test_ready_but_not_updated_replicas_do_not_count(self):
        half = workload(desired=2, updated=1, ready=2)
        verdict = deploy_verdict.decide(evidence(workloads=[half]))
        assert verdict.code == FAILED
        assert verdict.reason == "rollout-incomplete"

    def test_updated_but_not_ready_replicas_do_not_count(self):
        crashing = workload(desired=2, updated=2, ready=0)
        verdict = deploy_verdict.decide(evidence(workloads=[crashing]))
        assert verdict.code == FAILED
        assert verdict.reason == "rollout-incomplete"

    def test_missing_generation_fields_count_against_the_upgrade(self):
        unknown = workload(generation=None, observed=None)
        assert deploy_verdict._rolled_out(unknown) is False

    def test_missing_replica_count_counts_against_the_upgrade(self):
        unknown = workload(desired=None)
        assert deploy_verdict._rolled_out(unknown) is False

    def test_every_stalled_workload_is_named(self):
        verdict = deploy_verdict.decide(
            evidence(
                workloads=[
                    workload(name="deployment/xnat-web", observed=1),
                    workload(name="deployment/trust-api"),
                    workload(name="statefulset/omop-db", ready=0),
                ]
            )
        )
        assert "deployment/xnat-web" in verdict.detail
        assert "statefulset/omop-db" in verdict.detail
        assert "deployment/trust-api" not in verdict.detail


class TestConvergedAndHookFailures:
    def test_converged_release_passes_even_though_helm_failed(self):
        """Helm's wait expiring is not a failed upgrade when the release demonstrably landed."""
        verdict = deploy_verdict.decide(evidence())
        assert verdict.code == PASS
        assert verdict.reason == "converged-after-wait"

    def test_incomplete_xnat_init_is_its_own_verdict(self):
        verdict = deploy_verdict.decide(evidence(xnat_init_complete=False))
        assert verdict.code == HOOK_FAILED
        assert verdict.reason == "xnat-init-incomplete"
        assert "xnat-init" in verdict.detail

    def test_hook_failed_is_not_a_success_code(self):
        """#565 is reported, never swallowed: make must still fail on it."""
        assert HOOK_FAILED != PASS
        assert HOOK_FAILED != 0

    def test_unknown_hook_state_is_reported_as_incomplete(self):
        verdict = deploy_verdict.decide(evidence(xnat_init_complete=None))
        assert verdict.code == HOOK_FAILED

    def test_rollout_failure_outranks_the_hook_verdict(self):
        """A trust that never got the new spec is a failure whatever the init job did."""
        verdict = deploy_verdict.decide(evidence(workloads=[workload(observed=1)], xnat_init_complete=False))
        assert verdict.code == FAILED


class TestGatherParsing:
    def _cluster(self, monkeypatch, workloads, jobs, revision=5):
        def fake_json(args):
            if "deploy,statefulset,daemonset" in args:
                return workloads
            if "job" in args:
                return jobs
            return None

        monkeypatch.setattr(deploy_verdict, "_json", fake_json)
        monkeypatch.setattr(deploy_verdict, "release_revision", lambda helm, release: revision)
        return deploy_verdict.gather(["helm"], ["kubectl"], "flip-trust", helm_rc=1, revision_before=4)

    def test_deployment_rows_come_from_spec_replicas_and_status(self, monkeypatch):
        items = {
            "items": [
                {
                    "kind": "Deployment",
                    "metadata": {"name": "trust-api", "generation": 3},
                    "spec": {"replicas": 2},
                    "status": {"observedGeneration": 3, "updatedReplicas": 2, "readyReplicas": 2},
                }
            ]
        }
        result = self._cluster(monkeypatch, items, {"items": []})
        assert result["workloads"] == [
            {
                "name": "deployment/trust-api",
                "generation": 3,
                "observedGeneration": 3,
                "desired": 2,
                "updated": 2,
                "ready": 2,
            }
        ]
        assert deploy_verdict.decide(result).code == PASS

    def test_daemonset_rows_use_the_daemonset_status_fields(self, monkeypatch):
        items = {
            "items": [
                {
                    "kind": "DaemonSet",
                    "metadata": {"name": "node-agent", "generation": 1},
                    "spec": {},
                    "status": {
                        "observedGeneration": 1,
                        "desiredNumberScheduled": 3,
                        "updatedNumberScheduled": 3,
                        "numberReady": 3,
                    },
                }
            ]
        }
        result = self._cluster(monkeypatch, items, {"items": []})
        assert result["workloads"][0]["desired"] == 3
        assert result["workloads"][0]["ready"] == 3

    def test_absent_hook_job_means_it_completed_and_was_cleaned_up(self, monkeypatch):
        """hook-delete-policy is hook-succeeded, so a successful job leaves nothing behind."""
        items = {
            "items": [
                {
                    "kind": "Deployment",
                    "metadata": {"name": "a", "generation": 1},
                    "spec": {"replicas": 1},
                    "status": {"observedGeneration": 1, "updatedReplicas": 1, "readyReplicas": 1},
                }
            ]
        }
        result = self._cluster(monkeypatch, items, {"items": []})
        assert result["xnat_init_complete"] is True

    def test_a_job_still_present_without_a_complete_condition_is_incomplete(self, monkeypatch):
        jobs = {"items": [{"status": {"conditions": [{"type": "Failed", "status": "True"}]}}]}
        items = {
            "items": [
                {
                    "kind": "Deployment",
                    "metadata": {"name": "a", "generation": 1},
                    "spec": {"replicas": 1},
                    "status": {"observedGeneration": 1, "updatedReplicas": 1, "readyReplicas": 1},
                }
            ]
        }
        result = self._cluster(monkeypatch, items, jobs)
        assert result["xnat_init_complete"] is False
        assert deploy_verdict.decide(result).code == HOOK_FAILED

    def test_unreadable_workloads_leave_the_key_absent(self, monkeypatch):
        result = self._cluster(monkeypatch, None, None)
        assert "workloads" not in result
        assert deploy_verdict.decide(result).code == FAILED

    def test_the_job_query_is_scoped_to_this_release(self, monkeypatch):
        """Another release's xnat-init in the same namespace must not answer for this one."""
        seen = []

        def fake_json(args):
            seen.append(args)
            return None

        monkeypatch.setattr(deploy_verdict, "_json", fake_json)
        monkeypatch.setattr(deploy_verdict, "release_revision", lambda helm, release: 5)
        deploy_verdict.gather(["helm"], ["kubectl"], "flip-trust", helm_rc=1, revision_before=4)

        job_selector = next(a[a.index("-l") + 1] for a in seen if "job" in a)
        assert "app.kubernetes.io/instance=flip-trust" in job_selector
        assert "app.kubernetes.io/component=xnat-init" in job_selector

    def test_release_revision_returns_none_when_helm_cannot_be_read(self, monkeypatch):
        monkeypatch.setattr(deploy_verdict, "_json", lambda args: None)
        assert deploy_verdict.release_revision(["helm"], "flip-trust") is None

    def test_release_revision_rejects_a_non_integer_version(self, monkeypatch):
        monkeypatch.setattr(deploy_verdict, "_json", lambda args: {"version": "five"})
        assert deploy_verdict.release_revision(["helm"], "flip-trust") is None


class TestCommandLine:
    def _run(self, ev):
        return subprocess.run(
            [
                sys.executable,
                str(VERDICT_PY),
                "--release",
                "flip-trust",
                "--namespace",
                "flip",
                "--helm-rc",
                "1",
                "--evidence-json",
                json.dumps(ev),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_exit_code_zero_for_a_converged_release(self):
        assert self._run(evidence()).returncode == PASS

    def test_exit_code_one_for_an_unapplied_spec(self):
        result = self._run(evidence(revision_before=5, revision_after=5))
        assert result.returncode == FAILED
        assert "❌" in result.stderr

    def test_exit_code_three_for_an_incomplete_hook(self):
        result = self._run(evidence(xnat_init_complete=False))
        assert result.returncode == HOOK_FAILED
        assert "⚠️" in result.stderr


class TestMakefileWiring:
    MAKEFILE = (CHART_DIR / "Makefile").read_text()

    def test_deploy_classifies_the_helm_exit_instead_of_propagating_it(self):
        """Every caller of `deploy` — including upgrade-trust-k8s — inherits the verdict."""
        assert "scripts/deploy_verdict.py" in self.MAKEFILE
        assert "--helm-rc" in self.MAKEFILE
        assert "--revision-before" in self.MAKEFILE

    def test_the_revision_probe_is_namespaced(self):
        """`helm status` without --namespace reads the default namespace and finds no release,
        which would make every upgrade look like a first install."""
        recipe = self.MAKEFILE.split("\ndeploy: preflight")[1].split("\nundeploy:")[0]
        probe = next(line for line in recipe.splitlines() if "helm" in line.lower() and "status" in line)
        assert "--namespace $(NAMESPACE)" in probe

    def test_the_guessing_comment_is_gone(self):
        """The ambiguity this issue removes must not survive as prose in the recipe."""
        assert "often a non-fatal post-install hook" not in self.MAKEFILE

    def test_deploy_trust_k8s_still_patches_secrets_before_re_exiting(self):
        """#595: trust-api must get its real key even when the deploy failed."""
        recipe = self.MAKEFILE.split("deploy-trust-k8s:")[1].split("\nundeploy-trust-k8s")[0]
        assert "patch-kit-secrets" in recipe
        assert re.search(r"exit \$\$rc", recipe)

    def test_the_helm_timeout_is_still_one_knob(self):
        """FLIP#1228's rule: operators raise one budget, not a literal per wait site."""
        assert "--timeout $(HELM_TIMEOUT)" in self.MAKEFILE


@pytest.mark.parametrize("script", [VERDICT_PY])
def test_script_compiles(script):
    subprocess.run([sys.executable, "-m", "py_compile", str(script)], check=True, timeout=60)
