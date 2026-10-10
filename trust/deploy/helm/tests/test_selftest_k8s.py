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

"""The Kubernetes self-test's checks, judged against a fake cluster (FLIP#1390).

The node self-test (scripts/selftest_node.sh) is the acceptance checklist for every trust shape;
this is its Kubernetes form. Each check is a pure judgement over what the cluster answers, so the
fake below stands in for kubectl and helm and the tests pin what passes and what fails.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

CHART_DIR = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("selftest_k8s", CHART_DIR / "scripts" / "selftest_k8s.py")
selftest = importlib.util.module_from_spec(_spec)
# A dataclass resolves its annotations through sys.modules, so the module must be registered first.
sys.modules["selftest_k8s"] = selftest
_spec.loader.exec_module(selftest)


class FakeCluster:
    """Answers the questions selftest_k8s asks of a cluster, from canned values."""

    def __init__(self, **overrides):
        self.pods = {
            c: f"{c}-0"
            for c in ("trust-api", "imaging-api", "data-access-api", "omop-db", "orthanc", "xnat-web", "fl-client")
        }
        self.workload_items = [
            {
                "kind": "Deployment",
                "metadata": {"name": "trust-api"},
                "spec": {"replicas": 1},
                "status": {"readyReplicas": 1},
            },
            {
                "kind": "StatefulSet",
                "metadata": {"name": "omop-db"},
                "spec": {"replicas": 1},
                "status": {"readyReplicas": 1},
            },
            {
                "kind": "DaemonSet",
                "metadata": {"name": "alloy"},
                "status": {"desiredNumberScheduled": 1, "numberReady": 1},
            },
        ]
        self.health = '{"status":"ok","version":"sha-1"}'
        self.person_count = "4166"
        self.orthanc_creds = "flip:s3cret"
        self.orthanc_statistics = '{"CountInstances": 250, "CountStudies": 25}'
        self.orthanc_anonymous_code = "401"
        self.kit_present = True
        self.fl_states = [(True, 0), (True, 0)]
        self.exec_log = []
        self.__dict__.update(overrides)

    def workloads(self):
        return self.workload_items

    def pod(self, component):
        return self.pods.get(component)

    def container_port(self, pod):
        return 8000

    def orthanc_credentials(self):
        return self.orthanc_creds

    def container_state(self, pod):
        return self.fl_states.pop(0)

    def exec(self, pod, script, stdin=None):
        self.exec_log.append((pod, script, stdin))
        if "/health" in script:
            return (self.health is not None), self.health or "connection refused"
        if "omop.person" in script:
            return (self.person_count is not None), self.person_count or "relation does not exist"
        if "/statistics" in script:
            return (self.orthanc_statistics is not None), self.orthanc_statistics or "401"
        if "http_code" in script:
            return True, self.orthanc_anonymous_code
        if "fed_client.json" in script or "ca.crt" in script:
            return self.kit_present, "" if self.kit_present else "missing"
        raise AssertionError(f"unexpected exec: {script}")


# ── release ready ───────────────────────────────────────────────────────────────────


def test_a_release_whose_workloads_are_all_ready_passes():
    check = selftest.check_release_ready(FakeCluster())

    assert check.ok, check.detail


def test_a_workload_short_of_its_replicas_fails_and_is_named():
    cluster = FakeCluster()
    cluster.workload_items[1]["status"] = {"readyReplicas": 0}

    check = selftest.check_release_ready(cluster)

    assert not check.ok
    assert "omop-db" in check.detail


def test_a_release_with_no_workloads_fails():
    assert not selftest.check_release_ready(FakeCluster(workload_items=[])).ok


# ── FL kit ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("backend", ["nvflare", "flower"])
def test_the_kit_check_looks_for_the_backends_own_kit_files(backend):
    cluster = FakeCluster()

    check = selftest.check_fl_kit(cluster, backend)

    assert check.ok, check.detail
    script = cluster.exec_log[-1][1]
    assert ("fed_client.json" in script) == (backend == "nvflare")
    assert ("ca.crt" in script) == (backend == "flower")


def test_a_client_without_its_kit_fails():
    assert not selftest.check_fl_kit(FakeCluster(kit_present=False), "nvflare").ok


def test_no_fl_client_pod_fails_the_kit_check():
    cluster = FakeCluster()
    del cluster.pods["fl-client"]

    assert not selftest.check_fl_kit(cluster, "nvflare").ok


# ── API health ──────────────────────────────────────────────────────────────────────


def test_an_api_answering_status_ok_passes():
    check = selftest.check_health(FakeCluster(), "trust-api")

    assert check.ok, check.detail
    assert check.name == "trust-api health"


@pytest.mark.parametrize("body", ['{"status":"degraded"}', "not json", None])
def test_an_api_not_answering_status_ok_fails(body):
    assert not selftest.check_health(FakeCluster(health=body), "imaging-api").ok


# ── seeding ─────────────────────────────────────────────────────────────────────────


def test_omop_with_people_is_seeded():
    assert selftest.check_omop_seeded(FakeCluster()).ok


@pytest.mark.parametrize("count", ["0", None, "ERROR: x"])
def test_omop_without_people_is_not_seeded(count):
    assert not selftest.check_omop_seeded(FakeCluster(person_count=count)).ok


def test_orthanc_holding_instances_is_seeded_and_the_credential_goes_on_stdin():
    cluster = FakeCluster()

    seeded, _auth = selftest.check_orthanc(cluster)

    assert seeded.ok, seeded.detail
    stats_call = next(call for call in cluster.exec_log if "/statistics" in call[1])
    assert stats_call[2] == "flip:s3cret"
    assert "s3cret" not in stats_call[1], "the credential would show in the pod's process list"


def test_an_empty_orthanc_is_not_seeded():
    seeded, _ = selftest.check_orthanc(FakeCluster(orthanc_statistics='{"CountInstances": 0}'))

    assert not seeded.ok


# ── Orthanc auth ────────────────────────────────────────────────────────────────────


def test_orthanc_refusing_an_anonymous_request_passes():
    _, auth = selftest.check_orthanc(FakeCluster())

    assert auth.ok, auth.detail
    assert auth.name == "orthanc requires auth"


@pytest.mark.parametrize("code", ["200", "000", ""])
def test_orthanc_answering_anonymously_or_not_at_all_fails(code):
    _, auth = selftest.check_orthanc(FakeCluster(orthanc_anonymous_code=code))

    assert not auth.ok


# ── C-STORE ─────────────────────────────────────────────────────────────────────────


def test_the_cstore_check_is_the_smokes_verdict_with_its_last_line():
    passed = selftest.check_cstore(
        lambda: (0, "\x1b[0;32m✓ received\x1b[0m\n\x1b[0;32m✅ C-STORE smoke passed\x1b[0m\n", "")
    )
    failed = selftest.check_cstore(lambda: (1, "▶ start\n\x1b[0;31m✗ no receiver-side evidence\x1b[0m\n", ""))

    assert passed.ok
    assert passed.detail == "✅ C-STORE smoke passed"
    assert not failed.ok
    assert failed.detail == "✗ no receiver-side evidence"


def test_kubectls_stderr_chatter_is_not_mistaken_for_the_verdict():
    """kubectl exec says which container it defaulted to on stderr; the smoke speaks on stdout."""
    noise = 'Defaulted container "tomcat" out of: tomcat, prepare-data-dirs (init)\n'

    check = selftest.check_cstore(lambda: (0, "✅ C-STORE smoke passed\n", noise))

    assert check.detail == "✅ C-STORE smoke passed"


def test_a_smoke_that_died_silently_on_stdout_reports_its_stderr():
    check = selftest.check_cstore(lambda: (127, "", "bash: smoke-cstore.sh: No such file or directory\n"))

    assert not check.ok
    assert "No such file" in check.detail


# ── FL client ───────────────────────────────────────────────────────────────────────


def test_a_client_running_with_no_new_restarts_is_steady():
    check = selftest.check_fl_client(
        FakeCluster(fl_states=[(True, 2), (True, 2)]), settle_seconds=0, sleep=lambda _: None
    )

    assert check.ok, check.detail


@pytest.mark.parametrize("states", [[(True, 0), (True, 1)], [(False, 0), (False, 0)], [(True, 0), (False, 1)]])
def test_a_crash_looping_or_stopped_client_is_not_steady(states):
    check = selftest.check_fl_client(FakeCluster(fl_states=states), settle_seconds=0, sleep=lambda _: None)

    assert not check.ok


def test_the_client_is_sampled_a_settle_period_apart():
    waited = []

    selftest.check_fl_client(FakeCluster(), settle_seconds=45, sleep=waited.append)

    assert waited == [45]


# ── the report ──────────────────────────────────────────────────────────────────────


def test_the_report_matches_the_node_self_tests_shape(tmp_path):
    checks = [selftest.Check("a", True, "fine"), selftest.Check("b", False, "broken | piped")]

    ok = selftest.write_report(
        checks, tmp_path, "nvflare", "2026-10-10T10:00:00Z", "2026-10-10T10:05:00Z", "20261010T100000Z"
    )

    assert ok is False
    report = json.loads((tmp_path / "selftest-k8s-nvflare-20261010T100000Z.json").read_text())
    assert report["ok"] is False
    assert report["backend"] == "nvflare"
    assert report["checks"][1] == {"name": "b", "ok": False, "detail": "broken | piped"}
    md = (tmp_path / "latest-k8s-nvflare.md").read_text()
    assert "FAILED" in md
    assert "| ❌ | b |" in md


def test_a_report_with_no_checks_is_not_a_pass(tmp_path):
    assert selftest.write_report([], tmp_path, "flower", "s", "f", "x") is False
