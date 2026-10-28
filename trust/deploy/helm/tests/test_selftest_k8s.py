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

The VM node self-test (FLIP#1406) is the acceptance checklist for every trust shape;
this is its Kubernetes form. Each check is a pure judgement over what the cluster answers, so the
fake below stands in for kubectl and helm and the tests pin what passes and what fails.
"""

import base64
import importlib.util
import json
import subprocess
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
                "metadata": {"name": "trust-api", "generation": 2},
                "spec": {"replicas": 1},
                "status": {"observedGeneration": 2, "replicas": 1, "updatedReplicas": 1, "readyReplicas": 1},
            },
            {
                "kind": "StatefulSet",
                "metadata": {"name": "omop-db"},
                "spec": {"replicas": 1},
                "status": {
                    "replicas": 1,
                    "updatedReplicas": 1,
                    "readyReplicas": 1,
                    "currentRevision": "omop-db-2",
                    "updateRevision": "omop-db-2",
                },
            },
            {
                "kind": "DaemonSet",
                "metadata": {"name": "alloy"},
                "status": {"desiredNumberScheduled": 1, "updatedNumberScheduled": 1, "numberReady": 1},
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
    cluster.workload_items[1]["status"]["readyReplicas"] = 0

    check = selftest.check_release_ready(cluster)

    assert not check.ok
    assert "omop-db" in check.detail


@pytest.mark.parametrize(
    ("index", "status", "why"),
    [
        # helm upgrade with a crash-looping new pod: the old one is still Ready, so the count is met.
        (0, {"replicas": 2, "updatedReplicas": 1, "readyReplicas": 1}, "old replica"),
        (0, {"replicas": 1, "updatedReplicas": 0, "readyReplicas": 1}, "old replica"),
        (0, {"replicas": 0, "updatedReplicas": 0, "readyReplicas": 0}, "0/1 updated"),
        (0, {"observedGeneration": 1}, "not yet observed"),
        (1, {"currentRevision": "omop-db-1", "updateRevision": "omop-db-2"}, "update revision"),
        (2, {"updatedNumberScheduled": 0}, "0/1 updated"),
    ],
)
def test_a_release_still_rolling_out_fails_although_its_old_pods_are_ready(index, status, why):
    cluster = FakeCluster()
    cluster.workload_items[index]["status"].update(status)

    check = selftest.check_release_ready(cluster)

    assert not check.ok
    assert why in check.detail, check.detail


def test_the_pod_tested_is_a_ready_one_never_a_terminating_one(monkeypatch):
    def pod(name, ready, deleting=False):
        meta = {"name": name, **({"deletionTimestamp": "2026-10-14T00:00:00Z"} if deleting else {})}
        return {"metadata": meta, "status": {"conditions": [{"type": "Ready", "status": str(ready)}]}}

    cluster = selftest.KubectlCluster("flip-trust", "trust-release", None)
    items = [pod("old", True, deleting=True), pod("new-crashing", False), pod("new-ready", True)]
    monkeypatch.setattr(cluster, "_json", lambda _args: {"items": items})
    assert cluster.pod("fl-client") == "new-ready"
    items[:] = [pod("old", True, deleting=True)]
    assert cluster.pod("fl-client") is None


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


def test_an_inconclusive_smoke_fails_the_checklist():
    # smoke-cstore exits 2 when an arrival-time match is its only evidence (FLIP#1378). That is
    # a warning for an operator at a terminal, but an acceptance checklist must not pass on it.
    check = selftest.check_cstore(lambda: (2, "⚠ inconclusive: the only evidence is an arrival time\n", ""))

    assert not check.ok
    assert check.detail.startswith("⚠ inconclusive")


def test_kubectls_stderr_chatter_is_not_mistaken_for_the_verdict():
    """kubectl exec says which container it defaulted to on stderr; the smoke speaks on stdout."""
    noise = 'Defaulted container "tomcat" out of: tomcat, prepare-data-dirs (init)\n'

    check = selftest.check_cstore(lambda: (0, "✅ C-STORE smoke passed\n", noise))

    assert check.detail == "✅ C-STORE smoke passed"


def test_a_smoke_that_died_silently_on_stdout_reports_its_stderr():
    check = selftest.check_cstore(lambda: (127, "", "bash: smoke-cstore.sh: No such file or directory\n"))

    assert not check.ok
    assert "No such file" in check.detail


@pytest.mark.parametrize("error", [selftest.subprocess.TimeoutExpired("bash", 600), FileNotFoundError("bash")])
def test_a_smoke_that_cannot_run_is_a_failed_check_not_a_crash(monkeypatch, error):
    def boom(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(selftest.subprocess, "run", boom)

    check = selftest.check_cstore(lambda: selftest.run_smoke("flip-trust", "trust-release", None))

    assert not check.ok
    assert check.detail == str(error)


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


# ── dcm2niix conversion ─────────────────────────────────────────────────────────────


WRAPPER = "dcm2niix-scan"
COMMAND = f"/xapi/projects/FLIPSELFTEST/commands/7/wrappers/{WRAPPER}"


class FakeXnat:
    """XNAT and Orthanc as XnatPod reaches them, answering each call from a route table."""

    def __init__(self, **overrides):
        self.routes = {
            ("GET", "/xapi/commands?name=dcm2niix"): (200, json.dumps([{"id": 7, "xnat": [{"name": WRAPPER}]}])),
            ("GET", "/data/projects?format=json"): (200, json.dumps({"ResultSet": {"Result": [{"ID": "a-uuid"}]}})),
            ("DELETE", "/data/projects/FLIPSELFTEST?removeFiles=true"): (200, ""),
            ("POST", "/data/projects"): (200, ""),
            ("GET", "/data/experiments/XNAT_E00042/scans?format=json"): (
                200,
                json.dumps({"ResultSet": {"Result": [{"ID": "1"}]}}),
            ),
            ("PUT", f"{COMMAND}/enabled"): (200, ""),
            ("POST", f"{COMMAND}/root/scan/launch"): (
                200,
                json.dumps({"status": "success", "workflow-id": "wf-1"}),
            ),
            ("GET", "/data/experiments/XNAT_E00042/scans/1/resources/NIFTI/files?format=json"): (
                200,
                json.dumps({"ResultSet": {"Result": [{"Name": "selftest_1.nii.gz"}, {"Name": "selftest_1.json"}]}}),
            ),
        }
        # Successive answers to the experiment and container listings; the last one repeats.
        self.experiment_answers = [[{"ID": "XNAT_E00042"}]]
        self.container_answers = [
            [],
            [{"workflow-id": "wf-1", "status": "Running"}],
            [{"workflow-id": "wf-1", "status": "Complete"}],
        ]
        self.series = ("series-a", 1)
        self.import_answer = (200, "")
        self.calls = []
        self.__dict__.update(overrides)

    def request(self, method, path, body=None, content_type=""):
        self.calls.append((method, path, body))
        if (method, path) == ("GET", "/xapi/projects/FLIPSELFTEST/containers"):
            return 200, json.dumps(self._next(self.container_answers))
        if (method, path) == ("GET", "/data/projects/FLIPSELFTEST/experiments?format=json"):
            return 200, json.dumps({"ResultSet": {"Result": self._next(self.experiment_answers)}})
        if (method, path) not in self.routes:
            raise AssertionError(f"unexpected XNAT call: {method} {path}")
        return self.routes[(method, path)]

    @staticmethod
    def _next(answers):
        return answers.pop(0) if len(answers) > 1 else answers[0]

    def smallest_series(self, orthanc_creds):
        return self.series

    def import_series(self, orthanc_creds, series_id, project):
        self.calls.append(("IMPORT", series_id, project))
        return self.import_answer


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _convert(xnat, timeout=600, creds="flip:s3cret"):
    clock = Clock()
    return selftest.check_conversion(xnat, creds, timeout, poll=10, sleep=clock.sleep, clock=clock)


def test_a_series_converted_to_nifti_passes_and_the_project_is_deleted():
    xnat = FakeXnat()

    check = _convert(xnat)

    assert check.ok, check.detail
    assert "selftest_1.nii.gz" in check.detail
    deletes = [c for c in xnat.calls if c[0] == "DELETE"]
    assert len(deletes) == 1, "only the project this run created is deleted"
    assert xnat.calls[-1][0] == "DELETE"
    assert ("IMPORT", "series-a", "FLIPSELFTEST") in xnat.calls


def test_dcm2niix_is_launched_on_the_imported_scan():
    xnat = FakeXnat()

    _convert(xnat)

    launch = next(c for c in xnat.calls if c[1].endswith("/root/scan/launch"))
    assert json.loads(launch[2]) == {"scan": "/archive/experiments/XNAT_E00042/scans/1"}


@pytest.mark.parametrize(
    ("final", "expected"),
    [("Failed (Error)", "Failed (Error)"), ("Killed", "Killed")],
)
def test_a_container_that_ends_badly_fails_and_the_project_is_still_deleted(final, expected):
    xnat = FakeXnat(container_answers=[[{"workflow-id": "wf-1", "status": final}]])

    check = _convert(xnat)

    assert not check.ok
    assert expected in check.detail
    assert xnat.calls[-1][0] == "DELETE"


def test_a_conversion_that_never_finishes_times_out_naming_its_last_status():
    xnat = FakeXnat(container_answers=[[{"workflow-id": "wf-1", "status": "Created"}]])

    check = _convert(xnat, timeout=60)

    assert not check.ok
    assert "after 60s" in check.detail
    assert "Created" in check.detail
    assert "resourcequota" in check.detail
    assert xnat.calls[-1][0] == "DELETE"


def test_a_complete_container_without_a_nifti_file_fails():
    xnat = FakeXnat()
    xnat.routes[("GET", "/data/experiments/XNAT_E00042/scans/1/resources/NIFTI/files?format=json")] = (
        200,
        json.dumps({"ResultSet": {"Result": []}}),
    )

    check = _convert(xnat)

    assert not check.ok
    assert "no NIfTI" in check.detail


def test_another_projects_container_is_not_mistaken_for_ours():
    xnat = FakeXnat(
        container_answers=[
            [{"workflow-id": "wf-old", "status": "Complete"}, {"workflow-id": "wf-1", "status": "Running"}],
            [{"workflow-id": "wf-old", "status": "Complete"}, {"workflow-id": "wf-1", "status": "Failed"}],
        ]
    )

    check = _convert(xnat)

    assert not check.ok
    assert "Failed" in check.detail


def test_an_earlier_runs_container_is_not_read_while_ours_is_not_listed_yet():
    # XNAT keeps a deleted project's container rows, so the re-created project lists the last run's.
    xnat = FakeXnat(
        container_answers=[
            [{"workflow-id": "wf-old", "status": "Complete"}],
            [{"workflow-id": "wf-old", "status": "Complete"}, {"workflow-id": "wf-1", "status": "Failed"}],
        ]
    )

    check = _convert(xnat)

    assert not check.ok
    assert "Failed" in check.detail
    assert sum(1 for call in xnat.calls if call[1] == "/xapi/projects/FLIPSELFTEST/containers") == 2


@pytest.mark.parametrize(
    ("route", "answer", "says"),
    [
        (("GET", "/xapi/commands?name=dcm2niix"), (200, "[]"), "no dcm2niix command"),
        (("POST", "/data/projects"), (403, "forbidden"), "could not create"),
        (("PUT", f"{COMMAND}/enabled"), (500, "x"), "could not enable"),
        (
            ("POST", f"{COMMAND}/root/scan/launch"),
            (500, json.dumps({"status": "failure", "message": "no such wrapper"})),
            "refused the launch",
        ),
    ],
)
def test_each_step_that_fails_is_named(route, answer, says):
    xnat = FakeXnat()
    xnat.routes[route] = answer

    check = _convert(xnat)

    assert not check.ok
    assert says in check.detail


EXPERIMENTS = "/data/projects/FLIPSELFTEST/experiments?format=json"


def test_the_archived_session_is_read_once_right_after_the_import_with_no_wait():
    xnat = FakeXnat()

    assert _convert(xnat).ok
    order = [c[1] for c in xnat.calls]
    assert order.count(EXPERIMENTS) == 1
    assert order.index("series-a") < order.index(EXPERIMENTS)
    assert not any("prearchive" in str(c[1]) for c in xnat.calls)


def test_an_import_that_archives_nothing_fails_naming_the_project():
    xnat = FakeXnat(experiment_answers=[[]])

    check = _convert(xnat)

    assert not check.ok
    assert "archived no session into FLIPSELFTEST" in check.detail
    assert [c[1] for c in xnat.calls].count(EXPERIMENTS) == 1
    assert xnat.calls[-1][0] == "DELETE"


@pytest.mark.parametrize("row", [{"label": "no-id"}, "XNAT_E00042", None])
def test_a_session_row_without_an_id_fails_the_check_instead_of_raising(row):
    xnat = FakeXnat(experiment_answers=[[row]])

    check = _convert(xnat)

    assert not check.ok
    assert "listed a session in FLIPSELFTEST with no ID" in check.detail
    assert xnat.calls[-1][0] == "DELETE"


def test_a_scan_row_without_an_id_fails_the_check_instead_of_raising():
    xnat = FakeXnat()
    xnat.routes[("GET", "/data/experiments/XNAT_E00042/scans?format=json")] = (
        200,
        json.dumps({"ResultSet": {"Result": [{"type": "CT"}]}}),
    )

    check = _convert(xnat)

    assert not check.ok
    assert "listed a scan of session XNAT_E00042 with no ID" in check.detail
    assert xnat.calls[-1][0] == "DELETE"


def test_a_failed_import_fails_and_the_project_is_still_deleted():
    xnat = FakeXnat(import_answer=(0, "could not fetch the series from Orthanc"))

    check = _convert(xnat)

    assert not check.ok
    assert "did not import" in check.detail
    assert xnat.calls[-1][0] == "DELETE"


def test_a_project_that_cannot_be_deleted_is_reported_without_changing_the_verdict():
    xnat = FakeXnat()
    answers = iter([(500, "boom")])
    real = xnat.request

    def request(method, path, body=None, content_type=""):
        if method == "DELETE":
            xnat.calls.append((method, path, body))
            return next(answers)
        return real(method, path, body, content_type)

    xnat.request = request

    check = _convert(xnat)

    assert check.ok
    assert "could not delete the FLIPSELFTEST project" in check.detail


def _listing(*projects):
    return 200, json.dumps({"ResultSet": {"Result": list(projects)}})


def test_a_project_left_by_an_interrupted_run_is_cleared_first():
    xnat = FakeXnat()
    xnat.routes[("GET", "/data/projects?format=json")] = _listing({"ID": "FLIPSELFTEST", "name": "FLIP self-test"})

    assert _convert(xnat).ok
    methods = [c[0] for c in xnat.calls]
    assert methods.index("DELETE") < methods.index("POST"), "the stale project goes before the new one is made"
    assert methods.count("DELETE") == 2


def test_a_left_project_xnat_will_not_delete_fails_before_anything_is_imported():
    xnat = FakeXnat()
    xnat.routes[("GET", "/data/projects?format=json")] = _listing({"ID": "FLIPSELFTEST", "name": "FLIP self-test"})
    xnat.routes[("DELETE", "/data/projects/FLIPSELFTEST?removeFiles=true")] = (500, "x")

    check = _convert(xnat)

    assert not check.ok
    assert "could not clear" in check.detail
    assert not [c for c in xnat.calls if c[0] in ("POST", "IMPORT")]


def test_a_project_of_the_same_id_the_check_did_not_create_is_left_alone():
    xnat = FakeXnat()
    xnat.routes[("GET", "/data/projects?format=json")] = _listing({"ID": "FLIPSELFTEST", "name": "Real study"})

    check = _convert(xnat)

    assert not check.ok
    assert "did not create" in check.detail
    assert "Real study" in check.detail
    assert not [c for c in xnat.calls if c[0] in ("POST", "DELETE")]


def test_an_unreadable_project_listing_creates_and_deletes_nothing():
    xnat = FakeXnat()
    xnat.routes[("GET", "/data/projects?format=json")] = (401, "login page")

    check = _convert(xnat)

    assert not check.ok
    assert "could not list" in check.detail
    assert not [c for c in xnat.calls if c[0] in ("POST", "DELETE")]


def test_no_orthanc_credential_or_no_series_fails_before_anything_is_created():
    assert not _convert(FakeXnat(), creds=None).ok
    xnat = FakeXnat(series=None)
    check = _convert(xnat)
    assert not check.ok
    assert not [c for c in xnat.calls if c[0] in ("POST", "DELETE")]


# The shell XnatPod hands the pod, run for real against a stub curl: the scripts parse, and no
# credential appears on a command line. The password holds a quote, a backslash and a newline,
# the characters that would end a quoted curl config line early.
PASSWORD = 'n0t"a\\real\npassword'
STUB_CURL = """#!/bin/sh
printf '%s\\n' "$*" >> "$CURL_LOG.args"
cat >> "$CURL_LOG.config"
case "$*" in
  *tools/find*) printf '%s' "$FIND_ANSWER" ;;
  *-o*) for a; do [ "$prev" = "-o" ] && printf 'zip' > "$a"; prev="$a"; done ;;
  *) printf '%s\\n%s' "$BODY" "$CODE" ;;
esac
"""


class ShellCluster:
    """Runs XnatPod's scripts in a local sh, with the pod's environment and a stub curl."""

    def __init__(self, tmp_path, body='{"ok": true}', code="200", find_answer="[]"):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        curl = bin_dir / "curl"
        curl.write_text(STUB_CURL)
        curl.chmod(0o755)
        self.log = tmp_path / "curl"
        self.env = {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "XNAT_SERVICE_USER": "svc",
            "XNAT_SERVICE_PASSWORD": PASSWORD,
            "CURL_LOG": str(self.log),
            "BODY": body,
            "CODE": code,
            "FIND_ANSWER": find_answer,
        }

    def exec(self, pod, script, stdin=None):
        result = subprocess.run(["/bin/sh", "-c", script], input=stdin, capture_output=True, text=True, env=self.env)
        return result.returncode == 0, (result.stdout if result.returncode == 0 else result.stderr).strip()

    def args(self):
        return Path(f"{self.log}.args").read_text()

    def config(self):
        return Path(f"{self.log}.config").read_text()

    def credential(self):
        """The one config line curl was handed, decoded back to the credential it carries."""
        (line,) = self.config().splitlines()
        prefix, suffix = 'header = "Authorization: Basic ', '"'
        assert line.startswith(prefix), line
        assert line.endswith(suffix), line
        return base64.b64decode(line[len(prefix) : -len(suffix)]).decode()


def test_an_xnat_call_reads_status_and_body_and_keeps_the_credential_off_the_command_line(tmp_path):
    cluster = ShellCluster(tmp_path, body='{"a": 1}', code="201")

    status, body = selftest.XnatPod(cluster, "xnat-web-0").request("POST", "/data/projects", "<x/>", "application/xml")

    assert (status, body) == (201, '{"a": 1}')
    assert "password" not in cluster.args()
    assert cluster.credential() == f"svc:{PASSWORD}"
    assert "http://127.0.0.1:8080/data/projects" in cluster.args()


def test_the_smallest_series_is_chosen_and_orthancs_credential_goes_on_stdin(tmp_path):
    found = [{"ID": "big", "Instances": ["1", "2", "3"]}, {"ID": "small", "Instances": ["1"]}, {"ID": "empty"}]
    cluster = ShellCluster(tmp_path, find_answer=json.dumps(found))

    series = selftest.XnatPod(cluster, "xnat-web-0").smallest_series("flip:s3cret")

    assert series == ("small", 1)
    assert "s3cret" not in cluster.args()
    assert cluster.credential() == "flip:s3cret"


def test_the_import_posts_the_series_zip_into_the_project(tmp_path):
    cluster = ShellCluster(tmp_path, body="/data/archive/projects/FLIPSELFTEST", code="200")

    status, _ = selftest.XnatPod(cluster, "xnat-web-0").import_series("flip:s3cret", "series-a", "FLIPSELFTEST")

    assert status == 200
    args = cluster.args()
    assert "http://orthanc:8042/series/series-a/archive" in args
    assert "import-handler=DICOM-zip&PROJECT_ID=FLIPSELFTEST" in args
    assert "inbody=true" in args
    assert "action=commit&AA=true" in args
    assert "s3cret" not in args
    assert "password" not in args


def test_no_answer_from_xnat_is_status_zero(tmp_path):
    cluster = ShellCluster(tmp_path)
    cluster.env["PATH"] = "/nonexistent"

    status, _ = selftest.XnatPod(cluster, "xnat-web-0").request("GET", "/xapi/commands")

    assert status == 0


# ── the report ──────────────────────────────────────────────────────────────────────


def test_the_report_matches_the_node_self_tests_shape(tmp_path):
    checks = [selftest.Check("a", True, "fine"), selftest.Check("b", False, "broken | piped\nsecond line")]

    ok = selftest.write_report(
        checks, tmp_path, "nvflare", "2026-10-10T10:00:00Z", "2026-10-10T10:05:00Z", "20261010T100000Z"
    )

    assert ok is False
    report = json.loads((tmp_path / "selftest-k8s-nvflare-20261010T100000Z.json").read_text())
    assert report["ok"] is False
    assert report["backend"] == "nvflare"
    assert report["checks"][1] == {"name": "b", "ok": False, "detail": "broken | piped\nsecond line"}
    md = (tmp_path / "latest-k8s-nvflare.md").read_text()
    assert "FAILED" in md
    assert "| ❌ | b | broken \\| piped second line |" in md, "one row, three cells"


def test_a_report_with_no_checks_is_not_a_pass(tmp_path):
    assert selftest.write_report([], tmp_path, "flower", "s", "f", "x") is False
