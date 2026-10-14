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

A project's DICOM->NIfTI trigger fires once, when a scan is archived. A scan archived while the
trigger was missing or invalid (FLIP#1451), or whose conversion was lost (FLIP#1448: conversion Jobs
deleted to free a swamped Kubernetes node), keeps only its DICOM. Repairing the trigger
(``configure-dcm2niix.sh``, ``restore_nifti_triggers``) does not bring those scans back, because a
trigger never fires again for a scan already archived. This launches dcm2niix on them through
Container Service's own bulk launch: the route XNAT's bulk launcher posts to
(``commandUiLauncher.js``), which the batch-launch plugin's Processing Dashboard also hands its
selection to.

Run inside the imaging-api container::

    python -m imaging_api.scripts.reconvert_nifti                        # report only
    python -m imaging_api.scripts.reconvert_nifti --project A --launch   # convert project A's scans
    python -m imaging_api.scripts.reconvert_nifti --launch               # projects whose trigger is active

**What it converts:** an archived scan with a ``DICOM`` resource and no ``NIFTI`` resource. That is
what the ``dcm2niix-scan`` wrapper accepts (its matcher is ``'DICOM' in @.resources[*].label``) and
what it writes. A project id is its hub id (the id in the FLIP UI, XNAT's ``secondary_ID``) or its
XNAT project ID. Without ``--project``, ``--launch`` converts only projects whose trigger is active:
an inactive trigger means the hub's *DICOM to NIfTI* setting is off, and for a project with no trigger
the trust does not know the setting.

**How it knows a conversion worked:** by the scan's NIFTI resource. The launch response cannot say.
It holds one ``bulk-launch-id``, which XNAT records on workflows only, and every per-scan entry reads
"To be assigned" (container-service 3.8.1 ContainerServiceImpl.java:2349-2361). Containers are matched
to scans by their ``scan`` input instead. That tells a conversion still running from one that ended
without NIfTI.

**What it will not do:**

- Launch on a scan whose container has not finished, which would convert it twice.
  ``--relaunch-unfinished`` overrides this, for containers known to be dead (a finalize stall, a
  deleted Kubernetes Job).
- Launch a scan again once ``--max-attempts`` of its containers have ended without NIfTI.

While ``--wait`` lasts, it relaunches, within that bound, the scans whose conversion ended without
NIfTI. On Kubernetes a backlog queues behind the conversion cap (FLIP#1448) and can outlast the wait;
re-run the report to follow it. On Compose, ``make -C trust reconvert-nifti KIT=<CODE>`` runs this.
"""

import argparse
import json
import sys
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import requests

from imaging_api.config import get_settings
from imaging_api.routers.schemas import Project
from imaging_api.scripts.restore_nifti_triggers import (
    describe,
    flip_projects,
    resolve_ids,
    trigger_state,
    triggers_by_project,
)
from imaging_api.services.projects import (
    DCM2NIIX_INPUT_RESOURCE,
    DCM2NIIX_OUTPUT_RESOURCE,
    DCM2NIIX_ROOT_ELEMENT,
    _bounded_body,
    enable_wrapper_for_project,
    get_command_info,
    get_experiments,
)
from imaging_api.utils.auth import get_xnat_auth_headers
from imaging_api.utils.xnat_url import xnat_url

POLL_SECONDS = 30
# How many waiting scans the closing report names, so a large backlog does not bury the summary.
_MAX_LISTED_PENDING = 20
# The statuses after which Container Service does nothing more for a container: those its
# ``nonfinalized`` listing leaves out (container-service 3.8.1 ContainerEntityRepository.java,
# getNonFinalizedCriterion), except Finalizing, which that listing leaves out too, although the
# container is still uploading its output.
_ENDED_STATUSES = ("Complete", "Done", "Killed")


def has_ended(container: dict[str, Any]) -> bool:
    """Whether Container Service has finished with a container, successfully or not."""
    status = str(container.get("status") or "")
    return status in _ENDED_STATUSES or status.startswith("Failed")


@dataclass(frozen=True)
class Scan:
    """One archived scan.

    Attributes:
        experiment (str): The XNAT session (experiment) ID.
        label (str): The session's label, which FLIP sets to the study's accession number.
        scan (str): The scan's ID within the session.
    """

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
    """The scans of one archived session, with the labels of their resources.

    One ``GET /data/experiments/{id}?format=json``, whose item lists the session's scans
    (``scans/scan``), each with its resources (``file``).

    Args:
        experiment_id (str): The XNAT session ID.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        dict[str, set[str]]: Scan ID -> the labels of its resources.

    Raises:
        Exception: If XNAT does not answer with 200.
    """
    response = requests.get(xnat_url("data", "experiments", experiment_id, query={"format": "json"}), headers=headers)
    if response.status_code != 200:
        raise Exception(
            f"Error: reading XNAT session {experiment_id!r} failed: {response.status_code} - "
            f"{_bounded_body(response.text)}"
        )
    scans: dict[str, set[str]] = {}
    for item in response.json().get("items") or []:
        for child in item.get("children") or []:
            if child.get("field") != "scans/scan":
                continue
            for scan in child.get("items") or []:
                scans[str(scan["data_fields"]["ID"])] = {
                    str((resource.get("data_fields") or {}).get("label"))
                    for part in scan.get("children") or []
                    if part.get("field") == "file"
                    for resource in part.get("items") or []
                }
    return scans


def unconverted_scans(project_id: str, headers: dict[str, str]) -> tuple[list[Scan], int]:
    """The project's archived scans that have DICOM and no NIfTI.

    Args:
        project_id (str): XNAT project ID.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        tuple[list[Scan], int]: Those scans, and how many archived scans have DICOM at all.
    """
    missing: list[Scan] = []
    with_dicom = 0
    for experiment in get_experiments(project_id, headers):
        for scan_id, labels in session_scans(experiment.id, headers).items():
            if DCM2NIIX_INPUT_RESOURCE not in labels:
                continue
            with_dicom += 1
            if DCM2NIIX_OUTPUT_RESOURCE not in labels:
                missing.append(Scan(experiment.id, experiment.label, scan_id))
    return missing, with_dicom


def containers_by_scan(project_id: str, headers: dict[str, str]) -> dict[str, list[dict[str, Any]]]:
    """The project's Container Service containers, grouped by the scan their ``scan`` input names.

    ``GET /xapi/projects/{project}/containers`` (container-service 3.8.1 ContainerRestApi.java:115-125)
    lists every container, finished or not. Its ``nonfinalized`` filter is not used, because it drops
    containers that are Finalizing, which are still writing the NIfTI.

    Args:
        project_id (str): XNAT project ID.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        dict[str, list[dict[str, Any]]]: Scan URI -> the containers launched on that scan.

    Raises:
        Exception: If XNAT does not answer with 200.
    """
    response = requests.get(xnat_url("xapi", "projects", project_id, "containers"), headers=headers)
    if response.status_code != 200:
        raise Exception(
            f"Error: listing the containers of XNAT project '{project_id}' failed: {response.status_code} - "
            f"{_bounded_body(response.text)}"
        )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for container in response.json() or []:
        if not isinstance(container, dict):
            continue
        for given in container.get("inputs") or []:
            if (
                isinstance(given, dict)
                and given.get("type") == "wrapper-external"
                and given.get("name") == DCM2NIIX_ROOT_ELEMENT
                and given.get("value")
            ):
                grouped.setdefault(str(given["value"]), []).append(container)
                break
    return grouped


def bulk_launch(
    project_id: str, wrapper_id: int, scans: list[Scan], headers: dict[str, str]
) -> tuple[str, dict[str, str]]:
    """Launches a wrapper on every given scan in one call.

    ``POST /xapi/projects/{project}/wrappers/{wrapper}/root/scan/bulklaunch`` (container-service 3.8.1
    LaunchRestApi.java:418-429). The body is a map of strings, so the targets travel as one
    JSON-encoded list (ContainerServiceImpl.java:2324-2325). XNAT queues each target and returns: a
    success only means the launch was submitted.

    Args:
        project_id (str): XNAT project ID.
        wrapper_id (int): The wrapper's id, from ``get_command_info``.
        scans (list[Scan]): The scans to convert.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        tuple[str, dict[str, str]]: The bulk launch's id, and scan URI -> XNAT's message for each target
        it would not queue.

    Raises:
        Exception: If XNAT does not answer with 200.
    """
    response = requests.post(
        xnat_url(
            "xapi", "projects", project_id, "wrappers", str(wrapper_id), "root", DCM2NIIX_ROOT_ELEMENT, "bulklaunch"
        ),
        headers=headers,
        json={DCM2NIIX_ROOT_ELEMENT: json.dumps([scan.uri for scan in scans])},
    )
    if response.status_code != 200:
        raise Exception(
            f"Error: the bulk launch in XNAT project '{project_id}' failed: {response.status_code} - "
            f"{_bounded_body(response.text)}"
        )
    report = response.json() or {}
    refused = {
        str((failure.get("params") or {}).get(DCM2NIIX_ROOT_ELEMENT)): str(failure.get("message") or "no message")
        for failure in report.get("failures") or []
        if isinstance(failure, dict)
    }
    return str(report.get("bulk-launch-id") or "?"), refused


@dataclass
class _Followed:
    """What this run launched in one project, followed until each scan has NIfTI or is given up."""

    project: Project
    wrapper_id: int
    known: set[Any]  # ids of the containers on these scans before this run launched anything
    pending: dict[str, Scan]  # by URI
    launches: Counter[str] = field(default_factory=Counter)


def _name(container: dict[str, Any]) -> str:
    return f"container {container.get('id')} {container.get('status')} since {container.get('status-time')}"


def _list_scans(out: Callable[[str], None], scans: list[Scan], notes: dict[str, str]) -> None:
    """One report line per session, naming its scans and anything that holds one back."""
    sessions: dict[tuple[str, str], list[Scan]] = {}
    for scan in scans:
        sessions.setdefault((scan.label, scan.experiment), []).append(scan)
    for (label, experiment), group in sessions.items():
        named = ", ".join(s.scan + (f" [{notes[s.uri]}]" if s.uri in notes else "") for s in group)
        out(f"  {label} ({experiment}): scan{'s' if len(group) > 1 else ''} {named}")


def _not_launchable(project: Project, triggers: list[dict[str, Any]], named: set[str]) -> str | None:
    """Why ``--launch`` leaves a project alone unless it is named, or None when it may launch there."""
    if project.ID in named or any(t.get("active") for t in triggers):
        return None
    if triggers:
        return "its trigger is inactive, so its 'DICOM to NIfTI' setting on the hub is off"
    return (
        "it has no trigger, so this trust does not know its 'DICOM to NIfTI' setting on the hub "
        "(restore the trigger with restore_nifti_triggers)"
    )


def _at_least(value: str, least: int) -> int:
    number = int(value)
    if number < least:
        raise argparse.ArgumentTypeError(f"{value} is less than {least}")
    return number


def _minutes(value: str) -> int:
    return _at_least(value, 0)


def _attempts(value: str) -> int:
    return _at_least(value, 1)


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
        help="only this project (hub project id or XNAT project ID); --launch then converts it whatever its "
        "trigger says. Repeatable.",
    )
    parser.add_argument("--launch", action="store_true", help="launch the conversions listed; without it, report only")
    parser.add_argument(
        "--wait",
        type=_minutes,
        default=30,
        metavar="MINUTES",
        help="after launching, follow the conversions this long, relaunching those that end without NIfTI "
        "(default 30; 0 launches and returns)",
    )
    parser.add_argument(
        "--max-attempts",
        type=_attempts,
        default=3,
        metavar="N",
        help="how many of a scan's conversions may end without NIfTI before it is not launched again (default 3)",
    )
    parser.add_argument(
        "--relaunch-unfinished",
        action="store_true",
        help="also launch scans whose container has not finished; only for containers known to be dead "
        "(a finalize stall, a deleted Kubernetes Job)",
    )
    return parser.parse_args(argv)


def run(
    argv: Sequence[str],
    headers: dict[str, str],
    out: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Reports, and with ``--launch`` converts, the archived scans of FLIP projects that have no NIfTI.

    Args:
        argv (Sequence[str]): Command-line arguments, without the program name.
        headers (dict[str, str]): XNAT authentication headers for the service account.
        out (Callable[[str], None]): Where each report line goes.
        sleep (Callable[[float], None]): Waits between checks while following the conversions.
        clock (Callable[[], float]): Seconds on a monotonic clock, for the ``--wait`` budget.

    Returns:
        int: 0 when everything launched was converted (or nothing needed to be); 1 when a launch failed
        or a scan's conversions ended without NIfTI ``--max-attempts`` times; 2 for an unknown project
        id, in which case nothing is launched; 3 when the wait ended with conversions still running.
    """
    args = _parse(argv)
    projects = flip_projects(headers)
    named, unknown = resolve_ids(args.project, projects)
    if unknown:
        out(f"ERROR: no FLIP project on this XNAT has the id(s): {', '.join(unknown)}. Nothing was launched.")
        return 2

    triggers_of = triggers_by_project(headers)
    image = get_settings().DCM2NIIX_IMAGE
    tally: Counter[str] = Counter()
    followed: list[_Followed] = []
    selected = [p for p in projects if not named or p.ID in named]
    for project in selected:
        triggers = triggers_of.get(project.ID, [])
        states = "trigger " + "; ".join(trigger_state(t) for t in triggers) if triggers else "no trigger"
        # Containers before scans: a conversion that ends between the two reads then shows its NIfTI,
        # rather than looking like one that ended without it.
        by_scan = containers_by_scan(project.ID, headers)
        missing, with_dicom = unconverted_scans(project.ID, headers)
        head = f"{describe(project)}, {states}"
        if not missing:
            out(
                f"{head}: "
                + (
                    f"all {with_dicom} archived scans with DICOM have NIfTI."
                    if with_dicom
                    else "no archived scans with DICOM."
                )
            )
            continue
        out(f"{head}: {len(missing)} of {with_dicom} archived scans with DICOM have no NIfTI.")
        tally["missing"] += len(missing)

        ready: list[Scan] = []
        notes: dict[str, str] = {}
        for scan in missing:
            containers = by_scan.get(scan.uri, [])
            running = [c for c in containers if not has_ended(c)]
            ended = len(containers) - len(running)
            if running and not args.relaunch_unfinished:
                notes[scan.uri] = "converting: " + "; ".join(_name(c) for c in running[:3])
            elif ended >= args.max_attempts:
                notes[scan.uri] = f"{ended} conversions ended without NIfTI, not launched again"
            else:
                ready.append(scan)
                if running:
                    notes[scan.uri] = (
                        "unfinished: " + "; ".join(_name(c) for c in running[:3]) + "; --relaunch-unfinished"
                    )
        _list_scans(out, missing, notes)
        tally["held"] += len(missing) - len(ready)
        if not ready:
            out("  Nothing to launch.")
            continue
        reason = _not_launchable(project, triggers, named)
        if reason:
            tally["held"] += len(ready)
            out(f"  Not launched: {reason}. Name it with --project to convert it anyway.")
            continue

        try:
            # Refuses a wrapper on another image than this imaging-api's pin, as trigger creation does.
            command_id, wrapper_name, wrapper_id = get_command_info(project.ID, image, headers)
            if not args.launch:
                tally["would"] += len(ready)
                out(f"  Would launch {len(ready)} ('{wrapper_name}', wrapper {wrapper_id}).")
                continue
            enable_wrapper_for_project(project.ID, command_id, wrapper_name, headers)
            bulk_id, refused = bulk_launch(project.ID, wrapper_id, ready, headers)
        except Exception as exc:  # report and carry on: one project's failure must not strand the rest
            tally["failed"] += len(ready)
            out(f"  Cannot launch {len(ready)}: {exc}")
            continue
        for uri, message in refused.items():
            out(f"  XNAT did not queue {uri}: {message}")
        launched = [s for s in ready if s.uri not in refused]
        tally["failed"] += len(ready) - len(launched)
        tally["launched"] += len(launched)
        out(f"  Launched {len(launched)} (bulk launch {bulk_id}).")
        if launched:
            known = {c.get("id") for scan in missing for c in by_scan.get(scan.uri, [])}
            followed.append(
                _Followed(project, wrapper_id, known, {s.uri: s for s in launched}, Counter(s.uri for s in launched))
            )

    if followed and args.wait:
        _follow(followed, args, headers, out, sleep, clock, tally)
    elif followed:
        out("Not waiting (--wait 0): re-run the report to follow the conversions.")

    pending = [(f.project, s) for f in followed for s in f.pending.values()]
    tally["pending"] = len(pending)
    if pending:
        out(
            f"Still converting when the wait ended: {len(pending)}. Re-run the report to follow them; a new "
            "--launch leaves a scan alone while its container runs."
        )
        for project, scan in pending[:_MAX_LISTED_PENDING]:
            out(f"  {project.ID} {scan}")
        if len(pending) > _MAX_LISTED_PENDING:
            out(f"  ... and {len(pending) - _MAX_LISTED_PENDING} more.")

    summary = f"Projects: {len(selected)}. Scans without NIfTI: {tally['missing']}. "
    if args.launch:
        summary += (
            f"Launched: {tally['launched']}. Relaunched: {tally['relaunched']}. Converted: {tally['converted']}. "
            f"Still converting: {tally['pending']}. Ended without NIfTI: {tally['gave_up']}. "
        )
    else:
        summary += f"Would launch: {tally['would']}. "
    out(summary + f"Failed to launch: {tally['failed']}. Not launched: {tally['held']}.")
    if tally["failed"] or tally["gave_up"]:
        return 1
    return 3 if pending else 0


def _follow(
    followed: list[_Followed],
    args: argparse.Namespace,
    headers: dict[str, str],
    out: Callable[[str], None],
    sleep: Callable[[float], None],
    clock: Callable[[], float],
    tally: Counter[str],
) -> None:
    """Checks the launched scans until each has NIfTI or is given up, or the wait runs out.

    A scan whose conversion ended without NIfTI is relaunched while fewer than ``--max-attempts`` of its
    containers have ended. A scan is judged only once each of this run's launches of it has a container
    and those containers have all ended, so a launch still queued (behind the conversion cap on
    Kubernetes) or still running is never doubled.
    """
    out(f"Following the conversions for up to {args.wait} min, checking every {POLL_SECONDS} s.")
    start = clock()
    deadline = start + args.wait * 60
    shown: tuple[int, int] | None = None
    while any(f.pending for f in followed) and clock() < deadline:
        sleep(POLL_SECONDS)
        for f in followed:
            if f.pending:
                _check(f, args, headers, out, tally)
        progress = (tally["converted"], sum(len(f.pending) for f in followed))
        if progress != shown:
            shown = progress
            out(f"  After {(clock() - start) / 60:.1f} min: {progress[0]} converted, {progress[1]} still converting.")


def _check(
    f: _Followed, args: argparse.Namespace, headers: dict[str, str], out: Callable[[str], None], tally: Counter[str]
) -> None:
    """One check of one project's launched scans, relaunching those whose conversion ended without NIfTI."""
    # Containers before scans, for the reason given in run().
    by_scan = containers_by_scan(f.project.ID, headers)
    converted = {
        f"/archive/experiments/{experiment}/scans/{scan_id}"
        for experiment in {s.experiment for s in f.pending.values()}
        for scan_id, labels in session_scans(experiment, headers).items()
        if DCM2NIIX_OUTPUT_RESOURCE in labels
    }
    relaunch: list[Scan] = []
    for uri, scan in list(f.pending.items()):
        if uri in converted:
            del f.pending[uri]
            tally["converted"] += 1
            continue
        containers = by_scan.get(uri, [])
        mine = [c for c in containers if c.get("id") not in f.known]
        if len(mine) < f.launches[uri] or not all(has_ended(c) for c in mine):
            continue  # a launch has no container yet, or one is still running
        ended = sum(1 for c in containers if has_ended(c))
        last = _name(max(mine, key=lambda c: c.get("id") or 0))
        if ended < args.max_attempts:
            relaunch.append(scan)
            out(
                f"  {f.project.ID} {scan}: ended without NIfTI ({last}); "
                f"relaunching ({ended + 1} of {args.max_attempts})."
            )
        else:
            del f.pending[uri]
            tally["gave_up"] += 1
            out(
                f"  {f.project.ID} {scan}: {ended} conversions ended without NIfTI (last: {last}); not launched "
                "again. Container Service keeps each container's log."
            )
    if not relaunch:
        return
    try:
        bulk_id, refused = bulk_launch(f.project.ID, f.wrapper_id, relaunch, headers)
    except Exception as exc:  # stop following these scans; the next report lists them again
        for scan in relaunch:
            del f.pending[scan.uri]
        tally["failed"] += len(relaunch)
        out(f"  Relaunching {len(relaunch)} in {f.project.ID} FAILED: {exc}")
        return
    for scan in relaunch:
        if scan.uri in refused:
            del f.pending[scan.uri]
            tally["failed"] += 1
            out(f"  XNAT did not queue {scan.uri}: {refused[scan.uri]}")
        else:
            f.launches[scan.uri] += 1
            tally["relaunched"] += 1


def main() -> int:  # pragma: no cover - the container entry point; run() is what the tests drive
    # Flushed per line: under docker/kubectl exec stdout is a pipe, and --wait would hold the report back.
    return run(sys.argv[1:], get_xnat_auth_headers(), out=lambda line: print(line, flush=True))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
