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

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import Mock

from flip.nvflare.prune_job_pods import finished_at, prune

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
        _pod("pending", "Pending", 50),
    ]
    api = Mock()
    api.list_namespaced_pod.return_value = NS(items=pods)

    removed = prune(api, "flip-trust", "app.kubernetes.io/component=fl-job", KEEP, NOW)

    assert removed == ["old-success", "old-failure"]
    api.list_namespaced_pod.assert_called_once_with("flip-trust", label_selector="app.kubernetes.io/component=fl-job")
    assert [c.args for c in api.delete_namespaced_pod.call_args_list] == [
        ("old-success", "flip-trust"),
        ("old-failure", "flip-trust"),
    ]


def test_a_long_job_that_just_finished_is_kept():
    """Age counts from when the job stopped, not from when its pod was created."""
    pod = _pod("long-job", "Succeeded", created_hours_ago=40, finished_hours_ago=1)

    assert finished_at(pod) == NOW - timedelta(hours=1)


def test_a_pod_whose_containers_report_nothing_ages_from_its_creation():
    pod = _pod("never-started", "Failed", created_hours_ago=30)
    pod.status.container_statuses = None

    assert finished_at(pod) == NOW - timedelta(hours=30)
