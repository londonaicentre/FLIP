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

"""Finished job pods go once they are older than the keep period (FLIP#1428); running and recent ones stay."""

import runpy
import sys
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from flip.nvflare.prune_job_pods import INTERVAL_SECONDS, REQUEST_TIMEOUT_SECONDS, finished_at, keep_period, prune

NOW = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
KEEP = timedelta(hours=24)


def _pod(name: str, phase: str, created_hours_ago: float, finished_hours_ago: float | None = None) -> NS:
    terminated = None if finished_hours_ago is None else NS(finished_at=NOW - timedelta(hours=finished_hours_ago))
    statuses = [NS(state=NS(terminated=terminated))]
    return NS(
        metadata=NS(name=name, creation_timestamp=NOW - timedelta(hours=created_hours_ago)),
        status=NS(phase=phase, container_statuses=statuses),
    )


def test_prune_deletes_only_finished_pods_past_the_keep_period():
    pods = [
        _pod("old-success", "Succeeded", 50, finished_hours_ago=30),
        _pod("old-failure", "Failed", 50, finished_hours_ago=25),
        _pod("recent-success", "Succeeded", 50, finished_hours_ago=2),
        _pod("long-running", "Running", 50),
        _pod("just-pending", "Pending", 0.1),
    ]
    api = Mock()
    api.list_namespaced_pod.return_value = NS(items=pods)

    removed = prune(api, "flip-trust", "app.kubernetes.io/component=fl-job", KEEP, NOW)

    assert removed == ["old-success", "old-failure"]
    api.list_namespaced_pod.assert_called_once_with(
        "flip-trust", label_selector="app.kubernetes.io/component=fl-job", _request_timeout=REQUEST_TIMEOUT_SECONDS
    )
    assert [c.args for c in api.delete_namespaced_pod.call_args_list] == [
        ("old-success", "flip-trust"),
        ("old-failure", "flip-trust"),
    ]
    assert all(
        c.kwargs == {"_request_timeout": REQUEST_TIMEOUT_SECONDS} for c in api.delete_namespaced_pod.call_args_list
    )


def test_a_pod_that_never_ran_goes_once_it_is_older_than_the_keep_period():
    """A parent restart leaves a waiting job pod with nobody to time it out."""
    pods = [_pod("orphan-pending", "Pending", 30), _pod("orphan-unknown", "Unknown", 30)]
    for pod in pods:
        pod.status.container_statuses = None
    api = Mock()
    api.list_namespaced_pod.return_value = NS(items=pods)

    assert prune(api, "flip-trust", "sel", KEEP, NOW) == ["orphan-pending", "orphan-unknown"]


def test_a_delete_that_fails_is_named_and_the_rest_of_the_pass_goes_on(capsys):
    pods = [_pod("already-gone", "Succeeded", 50, 30), _pod("old-failure", "Failed", 50, 30)]
    api = Mock()
    api.list_namespaced_pod.return_value = NS(items=pods)
    api.delete_namespaced_pod.side_effect = [RuntimeError("404 Not Found"), None]

    assert prune(api, "flip-trust", "sel", KEEP, NOW) == ["old-failure"]
    assert "Could not delete job pod already-gone: RuntimeError('404 Not Found')" in capsys.readouterr().err


@pytest.mark.parametrize(("raw", "hours"), [(None, 24), ("36", 36), ("0", 0), ("soon", 24), ("-1", 24), ("inf", 24)])
def test_the_keep_period_falls_back_to_a_day_when_the_setting_is_unusable(raw, hours):
    """A bad value must not crash-loop the container, and a negative one must not delete every pod at once."""
    assert keep_period(raw) == timedelta(hours=hours)


def test_a_long_job_that_just_finished_is_kept():
    """Age counts from when the job stopped, not from when its pod was created."""
    pod = _pod("long-job", "Succeeded", created_hours_ago=40, finished_hours_ago=1)

    assert finished_at(pod) == NOW - timedelta(hours=1)


def test_a_pod_whose_containers_report_nothing_ages_from_its_creation():
    pod = _pod("never-started", "Failed", created_hours_ago=30)
    pod.status.container_statuses = None

    assert finished_at(pod) == NOW - timedelta(hours=30)


class _StopLoop(Exception):
    """Raised by the patched sleep to end the module's endless loop."""


def test_the_module_prunes_with_its_own_service_account_hourly_and_survives_a_failed_pass(monkeypatch, capsys):
    """Run as the chart runs it: in-cluster config, env-driven settings, one pass that deletes, one that fails."""
    old = _pod("old-success", "Succeeded", 100, finished_hours_ago=48)
    api = Mock()
    api.list_namespaced_pod.side_effect = [NS(items=[old]), RuntimeError("API server unreachable")]
    config = Mock()
    kubernetes = NS(client=NS(CoreV1Api=Mock(return_value=api)), config=config)
    monkeypatch.setitem(sys.modules, "kubernetes", kubernetes)
    monkeypatch.delitem(sys.modules, "flip.nvflare.prune_job_pods")  # runpy runs it afresh as __main__
    monkeypatch.setenv("FL_K8S_NAMESPACE", "flip-trust")
    monkeypatch.setenv("FL_JOB_POD_SELECTOR", "app.kubernetes.io/component=fl-job")
    monkeypatch.setenv("FL_JOB_POD_KEEP_HOURS", "36")
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise _StopLoop

    monkeypatch.setattr(time, "sleep", sleep)

    with pytest.raises(_StopLoop):
        runpy.run_module("flip.nvflare.prune_job_pods", run_name="__main__")

    config.load_incluster_config.assert_called_once_with()
    api.list_namespaced_pod.assert_called_with(
        "flip-trust", label_selector="app.kubernetes.io/component=fl-job", _request_timeout=REQUEST_TIMEOUT_SECONDS
    )
    api.delete_namespaced_pod.assert_called_once_with(
        "old-success", "flip-trust", _request_timeout=REQUEST_TIMEOUT_SECONDS
    )
    assert sleeps == [INTERVAL_SECONDS, INTERVAL_SECONDS]
    out = capsys.readouterr()
    assert "Deleted 1 job pod(s): old-success" in out.out
    assert "Job pod pruning failed: RuntimeError('API server unreachable')" in out.err
