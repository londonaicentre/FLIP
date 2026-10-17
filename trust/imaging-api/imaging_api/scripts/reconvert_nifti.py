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
"""Converts the archived scans of FLIP projects that have DICOM but no NIfTI (FLIP#1456, item G3).

A trigger fires once, when a scan is archived, so a scan archived while it was missing or invalid
(FLIP#1451), or whose conversion was lost (FLIP#1448), keeps only its DICOM. This lists those scans
and, with ``--launch``, converts them through Container Service's own bulk launch: the route XNAT's
bulk launcher, and the batch-launch plugin's Processing Dashboard through it, posts to.

    python -m imaging_api.scripts.reconvert_nifti [--project ID ...] [--launch [--wait MINUTES]]

What it selects, what it will not launch, and its exit codes: trust/deploy/helm/TROUBLESHOOTING.md
§2.3e. On Compose, ``make -C trust reconvert-nifti KIT=<CODE>`` runs this.
"""

import argparse
import json
import re
import sys
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from imaging_api.config import get_settings
from imaging_api.routers.schemas import Project
from imaging_api.services.projects import (
    DCM2NIIX_INPUT_RESOURCE,
    DCM2NIIX_OUTPUT_RESOURCE,
    DCM2NIIX_ROOT_ELEMENT,
    DCM2NIIX_SUBSCRIPTION_NAME,
    DCM2NIIX_WRAPPER_NAME,
    _bounded_body,
    get_all_projects,
    get_command_info,
    get_experiments,
)
from imaging_api.utils.auth import get_xnat_auth_headers
from imaging_api.utils.xnat_url import XNAT_REQUEST_TIMEOUT, xnat_url

# FLIP's XNAT projects carry their hub project id, a UUID, as secondary_ID (to_create_project).
HUB_PROJECT_ID = re.compile(r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")


def flip_projects(headers: dict[str, str]) -> list[Project]:
    """The XNAT projects FLIP created, name-ordered.

    Args:
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        list[Project]: Every visible project whose secondary_ID is a hub project id.
    """
    return sorted(
        (p for p in get_all_projects(headers) if HUB_PROJECT_ID.match(p.secondary_ID)),
        key=lambda p: (p.name, p.ID),
    )


def triggers_by_project(headers: dict[str, str]) -> dict[str, list[dict[str, Any]]]:
    """FLIP's DICOM->NIfTI subscriptions, grouped by the projects they filter on.

    One ``GET /xapi/events/subscriptions``, which needs the site administrator role configure-xnat.sh
    gives the service account.

    Args:
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        dict[str, list[dict[str, Any]]]: XNAT project ID -> its subscriptions named
        ``DCM2NIIX_SUBSCRIPTION_NAME``.

    Raises:
        Exception: If XNAT does not answer the listing with 200, or answers null (the Event Service is off).
    """
    response = requests.get(xnat_url("xapi", "events", "subscriptions"), headers=headers, timeout=XNAT_REQUEST_TIMEOUT)
    if response.status_code != 200:
        raise Exception(
            f"Error: listing XNAT event subscriptions failed: {response.status_code} - {_bounded_body(response.text)}"
        )
    subscriptions = response.json()
    # XNAT lists null rather than the triggers while the Event Service is off, which is not "no triggers".
    if subscriptions is None:
        raise Exception("Error: XNAT's Event Service is off; run the configure step, which turns it on, then retry.")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for sub in subscriptions:
        if sub.get("name") == DCM2NIIX_SUBSCRIPTION_NAME:
            for project_id in (sub.get("event-filter") or {}).get("project-ids") or []:
                grouped.setdefault(project_id, []).append(sub)
    return grouped


def resolve_ids(ids: list[str], projects: list[Project]) -> tuple[set[str], list[str]]:
    """Maps hub or XNAT project ids to XNAT IDs, returning the unknown ones separately."""
    by_any = {p.ID: p.ID for p in projects} | {p.secondary_ID: p.ID for p in projects}
    known = {by_any[i] for i in ids if i in by_any}
    return known, [i for i in ids if i not in by_any]


def describe(project: Project) -> str:
    """How a report line names a project: its XNAT ID, its hub project id and its name."""
    return f"{project.ID} (hub project {project.secondary_ID}, {project.name!r})"


def trigger_state(trigger: dict[str, Any]) -> str:
    """A trigger's state for a report line: active or inactive, and why XNAT calls it invalid."""
    state = "active" if trigger.get("active") else "inactive"
    if trigger.get("valid") is False:
        return f"{state}, INVALID: {trigger.get('validation-message') or 'no message'}"
    return state


POLL_SECONDS = 30
# A session archived this recently may have its trigger's conversion still on the way to a container,
# so its scans would look unconverted and be launched twice. Such sessions are left alone.
RECENT_MINUTES = 10
# What became of a launch, in the report's words.
CONVERTED, CONVERTING, NO_CONTAINER, ENDED = "converted", "converting", "without a container yet", "ended without NIfTI"
NOT_FOLLOWED = "not followed (--wait 0)"


def has_ended(container: dict[str, Any]) -> bool:
    """Whether Container Service is done with a container: the final statuses #1450's self-test reads."""
    return str(container.get("status") or "").startswith(("Complete", "Failed", "Killed"))


@dataclass(frozen=True)
class Scan:
    """One archived scan: its session's ID and label (FLIP sets the label to the accession number)."""

    experiment: str
    label: str
    scan: str

    @property
    def uri(self) -> str:
        """The scan's archive URI: what a launch takes and what a container's ``scan`` input holds."""
        return f"/archive/experiments/{self.experiment}/scans/{self.scan}"

    def __str__(self) -> str:
        return f"{self.label} ({self.experiment}) scan {self.scan}"


def session_scans(experiment_id: str, headers: dict[str, str]) -> dict[str, set[str]]:
    """Scan ID -> its resources' labels, for one archived session (``GET /data/experiments/{id}``).

    Raises:
        Exception: If XNAT does not answer with 200.
    """
    response = requests.get(
        xnat_url("data", "experiments", experiment_id, query={"format": "json"}),
        headers=headers,
        timeout=XNAT_REQUEST_TIMEOUT,
    )
    if response.status_code != 200:
        raise Exception(
            f"reading XNAT session {experiment_id!r} failed: {response.status_code} - {_bounded_body(response.text)}"
        )
    return {
        str(scan["data_fields"]["ID"]): {
            str((resource.get("data_fields") or {}).get("label"))
            for part in scan.get("children") or []
            if part.get("field") == "file"
            for resource in part.get("items") or []
        }
        for item in response.json().get("items") or []
        for child in item.get("children") or []
        if child.get("field") == "scans/scan"
        for scan in child.get("items") or []
    }


def _recent(insert_date: str, now: datetime) -> bool:
    # XNAT writes insert_date in its own zone, without naming it. It is read as UTC: an XNAT ahead of
    # UTC then makes a session look newer than it is, which only leaves it alone for longer.
    try:
        archived = datetime.fromisoformat(insert_date).replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return now - archived < timedelta(minutes=RECENT_MINUTES)


def unconverted_scans(project_id: str, headers: dict[str, str], now: datetime) -> tuple[list[Scan], int, int]:
    """The project's archived scans that have DICOM and no NIfTI, leaving recent sessions alone.

    Returns:
        tuple[list[Scan], int, int]: Those scans, how many scans have DICOM at all, and how many
        sessions were left alone for having been archived in the last ``RECENT_MINUTES``.
    """
    missing: list[Scan] = []
    with_dicom = recent = 0
    for experiment in get_experiments(project_id, headers):
        if _recent(experiment.insert_date, now):
            recent += 1
            continue
        for scan_id, labels in session_scans(experiment.id, headers).items():
            if DCM2NIIX_INPUT_RESOURCE in labels:
                with_dicom += 1
                if DCM2NIIX_OUTPUT_RESOURCE not in labels:
                    missing.append(Scan(experiment.id, experiment.label, scan_id))
    return missing, with_dicom, recent


def containers_by_scan(project_id: str, headers: dict[str, str]) -> dict[str, list[dict[str, Any]]]:
    """The project's containers, by the scan URI their ``scan`` input names, oldest first.

    ``GET /xapi/projects/{project}/containers`` lists every container. Its ``nonfinalized`` filter is
    not used: it leaves out containers that are Finalizing, which are still writing the NIfTI.

    Raises:
        Exception: If XNAT does not answer with 200.
    """
    response = requests.get(
        xnat_url("xapi", "projects", project_id, "containers"), headers=headers, timeout=XNAT_REQUEST_TIMEOUT
    )
    if response.status_code != 200:
        raise Exception(
            f"listing the containers of XNAT project '{project_id}' failed: {response.status_code} - "
            f"{_bounded_body(response.text)}"
        )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for container in response.json() or []:
        scan = next(
            (
                given.get("value")
                for given in container.get("inputs") or []
                if given.get("type") == "wrapper-external" and given.get("name") == DCM2NIIX_ROOT_ELEMENT
            ),
            None,
        )
        if scan:
            grouped.setdefault(str(scan), []).append(container)
    for containers in grouped.values():
        containers.sort(key=lambda c: c.get("id") or 0)
    return grouped


def bulk_launch(
    project_id: str, wrapper_id: int, scans: list[Scan], headers: dict[str, str]
) -> tuple[str, dict[str, str]]:
    """Launches a wrapper on the given scans in one call; XNAT queues each and answers at once.

    ``POST /xapi/projects/{project}/wrappers/{wrapper}/root/scan/bulklaunch`` (container-service 3.8.1
    LaunchRestApi.java:418-429) takes a map of strings, so the targets travel as one JSON-encoded list
    (ContainerServiceImpl.java:2324-2325).

    Returns:
        tuple[str, dict[str, str]]: The bulk launch's id, and scan URI -> XNAT's message for each target
        it did not queue.

    Raises:
        Exception: If XNAT does not answer with 200.
    """
    response = requests.post(
        xnat_url(
            "xapi", "projects", project_id, "wrappers", str(wrapper_id), "root", DCM2NIIX_ROOT_ELEMENT, "bulklaunch"
        ),
        headers=headers,
        json={DCM2NIIX_ROOT_ELEMENT: json.dumps([scan.uri for scan in scans])},
        timeout=XNAT_REQUEST_TIMEOUT,
    )
    if response.status_code != 200:
        raise Exception(
            f"the bulk launch in XNAT project '{project_id}' failed: {response.status_code} - "
            f"{_bounded_body(response.text)}"
        )
    report = response.json() or {}
    refused = {
        str((failure.get("params") or {}).get(DCM2NIIX_ROOT_ELEMENT)): str(failure.get("message") or "no message")
        for failure in report.get("failures") or []
    }
    return str(report.get("bulk-launch-id") or "?"), refused


def _name(container: dict[str, Any]) -> str:
    return f"container {container.get('id')} {container.get('status')} since {container.get('status-time')}"


def _not_launchable(project: Project, triggers: list[dict[str, Any]], named: set[str]) -> str | None:
    """Why ``--launch`` leaves a project alone unless it is named, or None when it may launch there."""
    if project.ID in named or any(t.get("active") for t in triggers):
        return None
    if triggers:
        return "its trigger is inactive, so its 'DICOM to NIfTI' setting on the hub is off"
    return "it has no trigger, so this trust does not know its 'DICOM to NIfTI' setting"


def _at_least(least: int) -> Callable[[str], int]:
    def number(value: str) -> int:
        if int(value) < least:
            raise argparse.ArgumentTypeError(f"{value} is less than {least}")
        return int(value)

    return number


def _parse(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m imaging_api.scripts.reconvert_nifti",
        description="Convert the archived scans of FLIP projects that have DICOM but no NIfTI (FLIP#1456).",
    )
    parser.add_argument(
        "--project",
        action="append",
        default=[],
        metavar="ID",
        help="only this project (hub or XNAT project id); --launch then converts it whatever its trigger says",
    )
    parser.add_argument("--launch", action="store_true", help="launch the conversions listed; without it, report only")
    parser.add_argument(
        "--wait",
        type=_at_least(0),
        default=30,
        metavar="MINUTES",
        help="after launching, follow the conversions this long (default 30; 0 does not follow)",
    )
    parser.add_argument(
        "--max-attempts",
        type=_at_least(1),
        default=3,
        metavar="N",
        help="leave a scan alone once N of its conversions have ended without NIfTI (default 3)",
    )
    parser.add_argument(
        "--relaunch-unfinished",
        action="store_true",
        help="also launch scans whose container has not finished, for containers known to be dead",
    )
    return parser.parse_args(argv)


def run(
    argv: Sequence[str],
    headers: dict[str, str],
    out: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> int:
    """Reports, and with ``--launch`` converts, the archived scans of FLIP projects that have no NIfTI.

    Args:
        argv (Sequence[str]): Command-line arguments, without the program name.
        headers (dict[str, str]): XNAT authentication headers for the service account.
        out (Callable[[str], None]): Where each report line goes.
        sleep (Callable[[float], None]): Waits between checks while following the conversions.
        clock (Callable[[], float]): Seconds on a monotonic clock, for the ``--wait`` budget.
        now (Callable[[], datetime]): The current UTC time, against which a session counts as recent.

    Returns:
        int: 0 when nothing failed and every launch was converted; 1 when a project could not be read,
        a launch failed, or a launched scan's conversion ended without NIfTI; 2 for an unknown project
        id (nothing is launched); 3 when the wait ended with launches not yet finished.
    """
    args = _parse(argv)
    projects = flip_projects(headers)
    named, unknown = resolve_ids(args.project, projects)
    if unknown:
        out(f"ERROR: no FLIP project on this XNAT has the id(s): {', '.join(unknown)}. Nothing was launched.")
        return 2
    triggers_of = triggers_by_project(headers)
    tally: Counter[str] = Counter()
    selected = [p for p in projects if not named or p.ID in named]

    # 1. Read and select, project by project: one project XNAT will not show must not stop the rest.
    to_launch: list[tuple[Project, list[Scan], dict[str, int]]] = []
    for project in selected:
        triggers = triggers_of.get(project.ID, [])
        head = describe(project) + (
            ", trigger " + "; ".join(map(trigger_state, triggers)) if triggers else ", no trigger"
        )
        try:
            # Containers before scans: a conversion that ends between the two reads then shows its NIfTI,
            # rather than looking like one that ended without it.
            by_scan = containers_by_scan(project.ID, headers)
            missing, with_dicom, recent = unconverted_scans(project.ID, headers, now())
        except Exception as exc:
            tally["unread"] += 1
            out(f"{head}: NOT READ: {exc}")
            continue
        tally["missing"] += len(missing)
        tally["recent"] += recent
        skipped = f"; {recent} session(s) archived in the last {RECENT_MINUTES} min left alone" if recent else ""
        out(f"{head}: {len(missing)} of {with_dicom} archived scans with DICOM have no NIfTI{skipped}.")

        ready: list[Scan] = []
        notes: dict[str, str] = {}
        for scan in missing:
            containers = by_scan.get(scan.uri, [])
            running = [c for c in containers if not has_ended(c)]
            ended = len(containers) - len(running)
            if running and not args.relaunch_unfinished:
                notes[scan.uri] = "converting: " + "; ".join(map(_name, running[:3]))
            elif ended >= args.max_attempts:
                notes[scan.uri] = f"{ended} conversions ended without NIfTI, not launched again"
            else:
                ready.append(scan)
        for scan in missing:
            out(f"  {scan}" + (f" [{notes[scan.uri]}]" if scan.uri in notes else ""))
        tally["held"] += len(missing) - len(ready)
        reason = _not_launchable(project, triggers, named) if ready else None
        if reason:
            tally["held"] += len(ready)
            out(f"  Not launched: {reason}. Name it with --project to convert it anyway.")
        elif ready:
            to_launch.append((project, ready, {s.uri: len(by_scan.get(s.uri, [])) for s in ready}))

    # 2. Launch: one wrapper lookup (the site listing, the same for every project), one bulk launch each.
    launched: list[tuple[str, Scan, int]] = []  # (project, scan, how many containers it had before)
    waiting = sum(len(ready) for _, ready, _ in to_launch)
    if waiting:
        try:
            wrapper_id = get_command_info(to_launch[0][0].ID, get_settings().DCM2NIIX_IMAGE, headers).wrapper_id
        except Exception as exc:  # e.g. the wrapper runs another image than this imaging-api's pin
            tally["failed"] += waiting
            out(f"Cannot launch {waiting}: {exc}")
            to_launch = []
        if to_launch and not args.launch:
            tally["would"] += waiting
            out(f"Would launch {waiting} ('{DCM2NIIX_WRAPPER_NAME}', wrapper {wrapper_id}).")
            to_launch = []
    for project, ready, before in to_launch:
        try:
            bulk_id, refused = bulk_launch(project.ID, wrapper_id, ready, headers)
        except Exception as exc:
            tally["failed"] += len(ready)
            out(f"{project.ID}: launching {len(ready)} FAILED: {exc}")
            continue
        for uri, message in refused.items():
            out(f"{project.ID}: XNAT did not queue {uri}: {message}")
        queued = [s for s in ready if s.uri not in refused]
        tally["failed"] += len(ready) - len(queued)
        launched += [(project.ID, s, before[s.uri]) for s in queued]
        out(f"{project.ID}: launched {len(queued)} (bulk launch {bulk_id}).")

    states = Counter({NOT_FOLLOWED: len(launched)})
    if launched and args.wait:
        states = _follow(launched, args.wait, headers, out, sleep, clock)
    summary = f"Projects: {len(selected)}. Scans without NIfTI: {tally['missing']}. "
    if args.launch:
        summary += f"Launched: {len(launched)}" + "".join(f", {n} {state}" for state, n in states.items() if n) + ". "
    else:
        summary += f"Would launch: {tally['would']}. "
    summary += f"Not launched: {tally['held']}. Failed to launch: {tally['failed']}."
    if tally["recent"]:
        summary += f" Recent sessions left alone: {tally['recent']}."
    if tally["unread"]:
        summary += f" Projects not read: {tally['unread']}."
    out(summary)
    if tally["unread"] or tally["failed"] or states[ENDED]:
        return 1
    return 3 if states[CONVERTING] or states[NO_CONTAINER] or states[NOT_FOLLOWED] else 0


def _follow(
    launched: list[tuple[str, Scan, int]],
    minutes: int,
    headers: dict[str, str],
    out: Callable[[str], None],
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> Counter[str]:
    """Follows this run's launches until each has NIfTI or its container has ended, or the wait runs out.

    A launch has its container once the scan has more containers than before it. Nothing is relaunched:
    a later ``--launch`` takes the ones that ended without NIfTI, within ``--max-attempts``.

    Returns:
        Counter[str]: How many launches are in each of CONVERTED, CONVERTING, NO_CONTAINER and ENDED.
    """
    state = {scan.uri: NO_CONTAINER for _, scan, _ in launched}
    deadline = clock() + minutes * 60
    shown: Counter[str] = Counter()
    while clock() < deadline and any(s in (NO_CONTAINER, CONVERTING) for s in state.values()):
        sleep(POLL_SECONDS)
        for project_id in dict.fromkeys(p for p, _, _ in launched):
            # A converted scan stays converted, so only the rest are read again.
            mine = [(scan, b) for p, scan, b in launched if p == project_id and state[scan.uri] != CONVERTED]
            if not mine:
                continue
            try:
                by_scan = containers_by_scan(project_id, headers)  # before the scans, as in run()
                converted = {
                    Scan(experiment, "", scan_id).uri
                    for experiment in {scan.experiment for scan, _ in mine}
                    for scan_id, labels in session_scans(experiment, headers).items()
                    if DCM2NIIX_OUTPUT_RESOURCE in labels
                }
            except Exception as exc:  # a transient error: the next check tries again
                out(f"  Checking {project_id} failed, trying again in {POLL_SECONDS} s: {exc}")
                continue
            for scan, before in mine:
                containers = by_scan.get(scan.uri, [])
                if scan.uri in converted:
                    state[scan.uri] = CONVERTED
                elif len(containers) > before:
                    state[scan.uri] = ENDED if has_ended(containers[-1]) else CONVERTING
        counts = Counter(state.values())
        if counts != shown:
            shown = counts
            out("  " + ", ".join(f"{n} {s}" for s, n in sorted(counts.items())) + ".")
    for _, scan, _ in launched:
        if state[scan.uri] != CONVERTED:
            out(f"  {scan}: {state[scan.uri]}")
    return Counter(state.values())


def main() -> int:  # pragma: no cover - the container entry point; run() is what the tests drive
    # Flushed per line: under docker/kubectl exec stdout is a pipe, and --wait would hold the report back.
    return run(sys.argv[1:], get_xnat_auth_headers(), out=lambda line: print(line, flush=True))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
