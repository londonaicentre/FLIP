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
"""Recreates the DICOM->NIfTI triggers an earlier XNAT configure deleted (FLIP#1451).

Until FLIP#1451 the Compose configure step deleted every project's dcm2niix event subscription each
time it ran, so on such a site no existing project converts its scans and nothing logs an error.
Whether a trigger is active is the project's ``dicom_to_nifti`` setting, which lives only on the hub,
so this recreates one only for the projects the operator names, active or inactive as the hub says,
through the same ``create_project_event_subscription`` project creation uses.

Run inside the imaging-api container::

    python -m imaging_api.scripts.restore_nifti_triggers                         # report only
    python -m imaging_api.scripts.restore_nifti_triggers --active A --inactive B  # restore A and B
    python -m imaging_api.scripts.restore_nifti_triggers --rest active --inactive B

An id is the project's hub id (XNAT's ``secondary_ID``) or its XNAT project ID. A project that still
has a trigger is never touched. On Compose, ``make -C trust restore-nifti-triggers KIT=<CODE>`` runs this.
"""

import argparse
import re
import sys
from collections.abc import Callable, Sequence
from typing import Any

import requests

from imaging_api.config import get_settings
from imaging_api.routers.schemas import Project
from imaging_api.services.projects import (
    DCM2NIIX_SUBSCRIPTION_NAME,
    _bounded_body,
    create_project_event_subscription,
    get_all_projects,
)
from imaging_api.utils.auth import get_xnat_auth_headers
from imaging_api.utils.xnat_url import xnat_url

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
    response = requests.get(xnat_url("xapi", "events", "subscriptions"), headers=headers)
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


def _parse(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m imaging_api.scripts.restore_nifti_triggers",
        description="Recreate the DICOM->NIfTI triggers of FLIP projects that have none (FLIP#1451).",
    )
    parser.add_argument(
        "--active",
        action="append",
        default=[],
        metavar="ID",
        help="restore this project's trigger active (its hub setting 'DICOM to NIfTI' is on). Repeatable.",
    )
    parser.add_argument(
        "--inactive",
        action="append",
        default=[],
        metavar="ID",
        help="restore this project's trigger inactive (its hub setting is off). Repeatable.",
    )
    parser.add_argument(
        "--rest",
        choices=["active", "inactive"],
        help="how to restore every project without a trigger that --active/--inactive do not name",
    )
    parser.add_argument("--dry-run", action="store_true", help="say what would be restored, change nothing")
    return parser.parse_args(argv)


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


def run(argv: Sequence[str], headers: dict[str, str], out: Callable[[str], None] = print) -> int:
    """Reports, and where told to restores, the trigger of every FLIP project.

    Args:
        argv (Sequence[str]): Command-line arguments, without the program name.
        headers (dict[str, str]): XNAT authentication headers for the service account.
        out (Callable[[str], None]): Where each report line goes.

    Returns:
        int: 0 when every requested restore succeeded, 1 when one failed, 2 for a bad request
        (an unknown id, or one named both active and inactive), in which case nothing is changed.
    """
    args = _parse(argv)
    projects = flip_projects(headers)
    active, unknown_active = resolve_ids(args.active, projects)
    inactive, unknown_inactive = resolve_ids(args.inactive, projects)
    unknown = unknown_active + unknown_inactive
    if unknown:
        out(f"ERROR: no FLIP project on this XNAT has the id(s): {', '.join(unknown)}. Nothing was changed.")
        return 2
    both = active & inactive
    if both:
        out(f"ERROR: named both --active and --inactive: {', '.join(sorted(both))}. Nothing was changed.")
        return 2

    # Read before anything is written, so a listing that fails changes nothing.
    triggers_of = triggers_by_project(headers)
    named = active | inactive
    image = get_settings().DCM2NIIX_IMAGE
    restored = failed = undecided = 0
    for project in projects:
        triggers = triggers_of.get(project.ID)
        if triggers:
            states = "; ".join(f"subscription {t.get('id')} {trigger_state(t)}" for t in triggers)
            note = " Named, but left as it is." if project.ID in named else ""
            out(f"{describe(project)}: has a trigger ({states}).{note}")
            continue
        if project.ID in named:
            make_active = project.ID in active
        elif args.rest:
            make_active = args.rest == "active"
        else:
            undecided += 1
            out(
                f"{describe(project)}: NO trigger, not restored. Name it with --active or --inactive, as its "
                "'DICOM to NIfTI' setting on the hub says, or pass --rest."
            )
            continue
        state = "active" if make_active else "inactive"
        if args.dry_run:
            out(f"{describe(project)}: NO trigger, would restore it {state}.")
            continue
        try:
            create_project_event_subscription(project.ID, image, make_active, headers)
        except Exception as exc:  # report and carry on: one project's failure must not strand the rest
            failed += 1
            out(f"{describe(project)}: NO trigger, restoring it {state} FAILED: {exc}")
            continue
        restored += 1
        out(f"{describe(project)}: NO trigger, restored it {state}.")

    out(
        f"Projects: {len(projects)}. Restored: {restored}. Failed: {failed}. "
        f"Without a trigger, not restored: {undecided}."
    )
    return 1 if failed else 0


def main() -> int:  # pragma: no cover - the container entry point; run() is what the tests drive
    return run(sys.argv[1:], get_xnat_auth_headers())


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
