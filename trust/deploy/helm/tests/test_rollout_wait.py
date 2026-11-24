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

"""The scoped rollout wait (FLIP#1645).

`helm upgrade --wait` is all-or-nothing and fails the whole upgrade on one unschedulable pod,
which two supported trust configurations have by design. These tests pin the behaviour that
replaces it: wait for the core workloads, report-but-do-not-wait for the optional ones, and fail
closed when the cluster cannot be read.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

CHART_DIR = Path(__file__).resolve().parent.parent
SCRIPT = CHART_DIR / "scripts" / "rollout_wait.py"

_spec = importlib.util.spec_from_file_location("rollout_wait", SCRIPT)
assert _spec is not None
assert _spec.loader is not None
rollout_wait = importlib.util.module_from_spec(_spec)
sys.modules["rollout_wait"] = rollout_wait
_spec.loader.exec_module(rollout_wait)


def wl(kind, name, component):
    return (kind, name, component)


# ---------------------------------------------------------------- the split


def test_core_services_are_waited_on():
    waited, skipped = rollout_wait.partition(
        [
            wl("deployment", "t-trust-api", "trust-api"),
            wl("deployment", "t-imaging-api", "imaging-api"),
            wl("deployment", "t-data-access-api", "data-access-api"),
            wl("statefulset", "t-omop-db", "omop-db"),
            wl("deployment", "t-orthanc", "orthanc"),
            wl("statefulset", "t-xnat-db", "xnat-db"),
            wl("deployment", "t-xnat-web", "xnat-web"),
            wl("deployment", "t-xnat-nginx", "xnat-nginx"),
        ]
    )
    assert skipped == []
    assert len(waited) == 8


@pytest.mark.parametrize(
    "component",
    ["fl-client", "monailabel", "grafana", "loki", "alloy"],
)
def test_optional_workloads_are_not_waited_on(component):
    """Each of these has a legitimate steady state of 'not ready' on a supported configuration."""
    waited, skipped = rollout_wait.partition([wl("deployment", f"t-{component}", component)])
    assert waited == []
    assert len(skipped) == 1


def test_the_fl_client_is_excluded_because_pending_is_its_resting_state():
    """The specific regression: an idle GPU fl-client must not fail an unattended upgrade."""
    waited, _ = rollout_wait.partition([wl("deployment", "t-fl-client", "fl-client")])
    assert waited == []


def test_an_unknown_component_is_waited_on_by_default():
    """Deny-list, not allow-list. A service added to the chart tomorrow is covered without anyone
    remembering to add it: forgetting to exclude an optional one is a loud timeout, while
    forgetting to include a core one would silently stop checking it."""
    waited, skipped = rollout_wait.partition([wl("deployment", "t-new-thing", "brand-new-service")])
    assert len(waited) == 1
    assert skipped == []


def test_a_workload_with_no_component_label_is_waited_on():
    waited, _ = rollout_wait.partition([wl("deployment", "t-unlabelled", "")])
    assert len(waited) == 1


def test_every_exclusion_carries_a_reason():
    """An entry with no stated reason is how an allow-list grows silently."""
    for component, reason in rollout_wait.OPTIONAL_COMPONENTS.items():
        assert reason.strip(), component


# ---------------------------------------------------------------- discovery


class FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _items(*names_and_components):
    return json.dumps(
        {
            "items": [
                {"metadata": {"name": n, "labels": {"app.kubernetes.io/component": c}}} for n, c in names_and_components
            ]
        }
    )


def test_discovery_selects_only_this_release(monkeypatch):
    """A deploy must not be held up by something else running in the namespace."""
    seen = []

    def fake_run(cmd, timeout):
        seen.append(cmd)
        return FakeProc(stdout=_items(("t-trust-api", "trust-api")))

    monkeypatch.setattr(rollout_wait, "_run", fake_run)
    rollout_wait.discover(["kubectl"], "flip", "t")
    for cmd in seen:
        assert "--selector" in cmd
        assert "app.kubernetes.io/instance=t" in cmd


def test_discovery_covers_every_workload_kind(monkeypatch):
    """omop-db, xnat-db and loki are StatefulSets; omitting the kind would skip the databases. A
    DaemonSet added to the chart is waited on unless named, like any other workload."""
    kinds = []

    def fake_run(cmd, timeout):
        kinds.append(cmd[2])
        return FakeProc(stdout=_items())

    monkeypatch.setattr(rollout_wait, "_run", fake_run)
    rollout_wait.discover(["kubectl"], "flip", "t")
    assert set(kinds) == {"deployment", "statefulset", "daemonset"}


def test_an_unreadable_cluster_raises_rather_than_reporting_success(monkeypatch):
    monkeypatch.setattr(rollout_wait, "_run", lambda cmd, timeout: FakeProc(returncode=1, stderr="nope"))
    with pytest.raises(RuntimeError):
        rollout_wait.discover(["kubectl"], "flip", "t")


def test_malformed_kubectl_output_raises(monkeypatch):
    monkeypatch.setattr(rollout_wait, "_run", lambda cmd, timeout: FakeProc(stdout="not json"))
    with pytest.raises(RuntimeError):
        rollout_wait.discover(["kubectl"], "flip", "t")


# ---------------------------------------------------------------- timeout parsing


@pytest.mark.parametrize(
    ("text", "expected"),
    [("30m", 1800), ("600s", 600), ("1h", 3600), ("90", 90)],
)
def test_durations_are_parsed_for_the_subprocess_budget(text, expected):
    assert rollout_wait._seconds(text) == expected


def test_an_unparseable_duration_falls_back_rather_than_crashing():
    assert rollout_wait._seconds("soon") == 1800


# ---------------------------------------------------------------- the verdict


def _main(monkeypatch, workloads, failures=(), argv_extra=()):
    monkeypatch.setattr(rollout_wait, "discover", lambda *a, **k: workloads)
    monkeypatch.setattr(
        rollout_wait,
        "rollout_status",
        lambda kubectl, ns, kind, name, timeout: (name not in failures, "rolled out"),
    )
    monkeypatch.setattr(rollout_wait, "is_ready", lambda *a, **k: False)
    monkeypatch.setattr(sys, "argv", ["rollout_wait.py", "--namespace", "flip", "--release", "t", *argv_extra])
    return rollout_wait.main()


def test_all_core_workloads_ready_exits_zero(monkeypatch):
    assert _main(monkeypatch, [wl("deployment", "t-trust-api", "trust-api")]) == 0


def test_a_core_workload_that_does_not_roll_out_exits_one(monkeypatch):
    rc = _main(
        monkeypatch,
        [wl("deployment", "t-trust-api", "trust-api")],
        failures={"t-trust-api"},
    )
    assert rc == 1


def test_an_unready_optional_workload_does_not_fail_the_deploy(monkeypatch, capsys):
    """The whole point of the change: an idle fl-client is reported, not fatal."""
    rc = _main(monkeypatch, [wl("deployment", "t-fl-client", "fl-client")])
    assert rc == 0
    assert "not waited on" in capsys.readouterr().out


def test_an_unready_optional_workload_is_still_reported(monkeypatch, capsys):
    """'Not waited on' must never mean 'not looked at'."""
    _main(monkeypatch, [wl("deployment", "t-fl-client", "fl-client")])
    out = capsys.readouterr().out
    assert "t-fl-client" in out
    assert "idle or awaiting its kit" in out


def test_a_failing_core_workload_fails_even_when_optionals_are_fine(monkeypatch):
    rc = _main(
        monkeypatch,
        [
            wl("deployment", "t-trust-api", "trust-api"),
            wl("deployment", "t-fl-client", "fl-client"),
        ],
        failures={"t-trust-api"},
    )
    assert rc == 1


def test_a_release_with_no_workloads_fails_closed(monkeypatch):
    """Finding nothing means the release was not understood, not that it is healthy."""
    assert _main(monkeypatch, []) == 1


def test_an_undiscoverable_release_fails_closed(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no cluster")

    monkeypatch.setattr(rollout_wait, "discover", boom)
    monkeypatch.setattr(sys, "argv", ["rollout_wait.py", "--namespace", "flip", "--release", "t"])
    assert rollout_wait.main() == 1


# ---------------------------------------------------------------- the kubectl command


def test_a_context_pinned_kubectl_is_split_into_its_arguments(monkeypatch):
    """With KUBE_CONTEXT set the Makefile passes `kubectl --context <ctx>` as ONE argument; used as
    the program name it crashed every context-pinned deploy after helm had applied the release."""
    seen = []

    def fake_run(cmd, timeout):
        seen.append(cmd)
        if cmd[3] == "get":
            return FakeProc(stdout=_items(("t-trust-api", "trust-api")) if cmd[4] == "deployment" else _items())
        return FakeProc(stdout="rolled out")

    monkeypatch.setattr(rollout_wait, "_run", fake_run)
    monkeypatch.setattr(
        sys, "argv", ["rollout_wait.py", "--namespace", "flip", "--release", "t", "--kubectl", "kubectl --context x"]
    )
    assert rollout_wait.main() == 0
    assert seen
    for cmd in seen:
        assert cmd[:3] == ["kubectl", "--context", "x"], cmd


def test_a_missing_kubectl_fails_closed_without_a_traceback(monkeypatch, capsys):
    def missing(cmd, timeout):
        raise FileNotFoundError(2, "No such file or directory", cmd[0])

    monkeypatch.setattr(rollout_wait, "_run", missing)
    monkeypatch.setattr(sys, "argv", ["rollout_wait.py", "--namespace", "flip", "--release", "t", "--kubectl", "nope"])
    assert rollout_wait.main() == 1
    assert "could not determine" in capsys.readouterr().err


def test_a_kubectl_that_hangs_fails_that_workload_rather_than_crashing(monkeypatch, capsys):
    monkeypatch.setattr(rollout_wait, "discover", lambda *a, **k: [wl("deployment", "t-trust-api", "trust-api")])

    def hang(*a, **k):
        raise subprocess.TimeoutExpired("kubectl", 1)

    monkeypatch.setattr(rollout_wait, "rollout_status", hang)
    monkeypatch.setattr(sys, "argv", ["rollout_wait.py", "--namespace", "flip", "--release", "t"])
    assert rollout_wait.main() == 1
    assert "could not run kubectl" in capsys.readouterr().out


# ---------------------------------------------------------------- one shared budget


def test_the_timeout_is_one_budget_across_all_workloads(monkeypatch):
    """Waited one after another, a per-workload budget made the worst case N x HELM_TIMEOUT."""
    clock = [1000.0]
    budgets = []

    def slow(kubectl, ns, kind, name, seconds):
        budgets.append(seconds)
        clock[0] += 40
        return True, "rolled out"

    monkeypatch.setattr(rollout_wait.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        rollout_wait,
        "discover",
        lambda *a, **k: [wl("deployment", f"t-{n}", n) for n in ("a", "b", "c")],
    )
    monkeypatch.setattr(rollout_wait, "rollout_status", slow)
    monkeypatch.setattr(rollout_wait, "is_ready", lambda *a, **k: True)
    monkeypatch.setattr(sys, "argv", ["rollout_wait.py", "--namespace", "flip", "--release", "t", "--timeout", "100s"])
    rollout_wait.main()
    assert budgets == [100, 60, 20]


def test_a_daemonset_is_judged_on_its_scheduled_pods(monkeypatch):
    obj = {"status": {"desiredNumberScheduled": 3, "numberReady": 2}}
    monkeypatch.setattr(rollout_wait, "_run", lambda cmd, timeout: FakeProc(stdout=json.dumps(obj)))
    assert rollout_wait.is_ready(["kubectl"], "flip", "daemonset", "t-x") is False
    obj["status"]["numberReady"] = 3
    assert rollout_wait.is_ready(["kubectl"], "flip", "daemonset", "t-x") is True


# ---------------------------------------------------------------- an fl-client that was serving (FLIP#1984)


def test_an_fl_client_ready_before_the_upgrade_is_waited_on():
    waited, skipped = rollout_wait.partition(
        [wl("deployment", "t-fl-client", "fl-client")], frozenset({"deployment/t-fl-client"})
    )
    assert waited == [wl("deployment", "t-fl-client", "fl-client")]
    assert skipped == []


def test_ready_before_reaches_only_the_fl_client():
    """Observability and MONAI Label stay optional however they were before."""
    waited, _ = rollout_wait.partition([wl("deployment", "t-grafana", "grafana")], frozenset({"deployment/t-grafana"}))
    assert waited == []


def test_a_serving_fl_client_that_does_not_come_back_fails_the_deploy(monkeypatch, capsys):
    rc = _main(
        monkeypatch,
        [wl("deployment", "t-fl-client", "fl-client")],
        failures={"t-fl-client"},
        argv_extra=("--ready-before", "deployment/t-fl-client"),
    )
    assert rc == 1
    assert "it was ready before the upgrade" in capsys.readouterr().out


def test_report_ready_names_only_the_ready_fl_clients(monkeypatch, capsys):
    monkeypatch.setattr(
        rollout_wait,
        "discover",
        lambda *a, **k: [
            wl("deployment", "t-trust-api", "trust-api"),
            wl("deployment", "t-fl-client-net-1", "fl-client"),
            wl("deployment", "t-fl-client-net-2", "fl-client"),
        ],
    )
    monkeypatch.setattr(rollout_wait, "is_ready", lambda kubectl, ns, kind, name: name.endswith("net-1"))
    monkeypatch.setattr(sys, "argv", ["rollout_wait.py", "--namespace", "flip", "--release", "t", "--report-ready"])
    assert rollout_wait.main() == 0
    assert capsys.readouterr().out.strip() == "deployment/t-fl-client-net-1"


def test_report_ready_on_a_first_install_names_nothing(monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("no release")

    monkeypatch.setattr(rollout_wait, "discover", boom)
    monkeypatch.setattr(sys, "argv", ["rollout_wait.py", "--namespace", "flip", "--release", "t", "--report-ready"])
    assert rollout_wait.main() == 0
    assert capsys.readouterr().out.strip() == ""
