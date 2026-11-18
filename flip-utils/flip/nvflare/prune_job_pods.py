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

"""Delete finished NVFLARE job pods (FLIP#1428).

NVFLARE's Kubernetes launcher leaves a job pod behind when its job ends. The chart runs this beside the fl-client
parent when it enables job pods (``python -m flip.nvflare.prune_job_pods``): every hour it deletes this net's job
pods that finished more than ``FL_JOB_POD_KEEP_HOURS`` ago (default 24), so a day of logs stays for debugging.
A pod still Pending or Unknown after that long goes too: the parent that would have timed it out was restarted.
"""

import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

FINISHED_PHASES = ("Succeeded", "Failed")
# Never ran: aged from creation, which is what finished_at returns when no container has stopped.
ORPHANED_PHASES = ("Pending", "Unknown")
INTERVAL_SECONDS = 3600
DEFAULT_KEEP_HOURS = 24.0
# The kubernetes client has no default timeout, and a stalled call would stop the loop for good.
REQUEST_TIMEOUT_SECONDS = 30


def finished_at(pod: Any) -> datetime:
    """When the pod's last container stopped, or its creation time when no container says."""
    times: list[datetime] = [
        status.state.terminated.finished_at
        for status in pod.status.container_statuses or []
        if status.state and status.state.terminated and status.state.terminated.finished_at
    ]
    created: datetime = pod.metadata.creation_timestamp
    return max(times, default=created)


def prune(api: Any, namespace: str, selector: str, keep: timedelta, now: datetime) -> list[str]:
    """Delete the selected pods that finished, or never ran, more than ``keep`` ago; returns their names."""
    removed = []
    pods = api.list_namespaced_pod(namespace, label_selector=selector, _request_timeout=REQUEST_TIMEOUT_SECONDS)
    for pod in pods.items:
        if pod.status.phase not in FINISHED_PHASES + ORPHANED_PHASES or now - finished_at(pod) <= keep:
            continue
        try:
            api.delete_namespaced_pod(pod.metadata.name, namespace, _request_timeout=REQUEST_TIMEOUT_SECONDS)
        except Exception as e:  # one pod already gone or terminating must not skip the rest
            print(f"Could not delete job pod {pod.metadata.name}: {e!r}", file=sys.stderr, flush=True)
            continue
        removed.append(pod.metadata.name)
    return removed


def keep_period(raw: str | None) -> timedelta:
    """The keep period from ``FL_JOB_POD_KEEP_HOURS``; the default when it is unset, not a number or negative."""
    if raw is None:
        return timedelta(hours=DEFAULT_KEEP_HOURS)
    try:
        hours = float(raw)
        if hours < 0:
            raise ValueError
        return timedelta(hours=hours)
    except (ValueError, OverflowError):
        print(f"FL_JOB_POD_KEEP_HOURS={raw!r} is not a usable number; using {DEFAULT_KEEP_HOURS:g}", file=sys.stderr)
        return timedelta(hours=DEFAULT_KEEP_HOURS)


def main() -> None:
    """Prune hourly for the container's lifetime, with the pod's own service account."""
    from kubernetes import client, config

    namespace = os.environ["FL_K8S_NAMESPACE"]
    selector = os.environ["FL_JOB_POD_SELECTOR"]
    keep = keep_period(os.environ.get("FL_JOB_POD_KEEP_HOURS"))
    config.load_incluster_config()
    api = client.CoreV1Api()
    while True:
        try:
            removed = prune(api, namespace, selector, keep, datetime.now(timezone.utc))
            if removed:
                print(f"Deleted {len(removed)} job pod(s): {', '.join(removed)}", flush=True)
        except Exception as e:  # a failed pass must not end the loop; the next one retries
            print(f"Job pod pruning failed: {e!r}", file=sys.stderr, flush=True)
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
