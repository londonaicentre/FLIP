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

"""Keeps each project's imaging to its approved cohort as it stands now (FLIP#1860).

data-access-api serves a project's frozen membership that still resolves in OMOP (FLIP#857), so a patient who leaves
the cohort stops being served at once. Their imaging follows here, from the same list:

- a download is refused for an accession the list does not hold (``is_served``);
- ``reconcile`` removes such an accession's cached copies at once, and its XNAT session once it has been off the list
  for ``COHORT_REMOVAL_GRACE_HOURS``. A cached copy is downloaded again on demand, but only a new pull from the PACS
  refills XNAT, and the list is short for a while whenever the trust reloads its OMOP tables: hence the wait.

Nothing is removed while data-access-api cannot answer, or refuses the whole list: a refusal does not say whether the
cohort fell below the trust's threshold or its lookup failed.
"""

import asyncio
import time

import requests
from fastapi.concurrency import run_in_threadpool

from imaging_api.config import get_settings
from imaging_api.routers.schemas import Project
from imaging_api.services.image_cache import remove_accessions_except
from imaging_api.services.import_requests import delete_prearchive_session, get_prearchive_sessions
from imaging_api.services.projects import get_all_projects
from imaging_api.services_external.data_access import get_accession_ids
from imaging_api.utils.auth import get_xnat_auth_headers
from imaging_api.utils.encryption import PROJECT_ID_CONTEXT, encrypt
from imaging_api.utils.exceptions import CohortBelowThresholdError, XnatFetchError
from imaging_api.utils.logger import logger
from imaging_api.utils.xnat_url import XNAT_REQUEST_TIMEOUT, xnat_url

# How long a download trusts the list it last read. A training round asks once per study, and each read of the list
# is two scans of omop.image_occurrence.
SERVED_MAX_AGE_SECONDS = 60

# Module snapshot, as in download.py: tests patch this name.
BASE_IMAGES_DOWNLOAD_DIR = get_settings().BASE_IMAGES_DOWNLOAD_DIR

# Hub project id -> when its list was read, and the list (None when data-access-api released none).
_served: dict[str, tuple[float, frozenset[str] | None]] = {}

# (XNAT project id, accession) -> when a reconcile first found it off the list. Held in memory, so a restart starts
# the wait again: a removal can come late, never early.
_left_at: dict[tuple[str, str], float] = {}


async def served_accessions(
    central_hub_project_id: str, max_age: float = SERVED_MAX_AGE_SECONDS
) -> frozenset[str] | None:
    """The accessions data-access-api serves for the project now.

    Args:
        central_hub_project_id (str): The hub's project id, which the approved cohort is stored under.
        max_age (float): Seconds a list already read may be reused for; 0 reads it again.

    Returns:
        frozenset[str] | None: The accessions, or None when data-access-api refuses to release any (the cohort is
        below the trust's threshold, has no approved membership, or its lookup failed: it does not say which).

    Raises:
        RuntimeError: If data-access-api cannot answer.
    """
    read_at, served = _served.get(central_hub_project_id, (float("-inf"), None))
    if time.monotonic() - read_at < max_age:
        return served
    encrypted_project_id = encrypt(central_hub_project_id, context=PROJECT_ID_CONTEXT)
    try:
        # No SQL to send: data-access-api serves the approved cohort whatever the caller's query says.
        served = frozenset(await get_accession_ids(encrypted_project_id, ""))
    except CohortBelowThresholdError:
        served = None
    _served[central_hub_project_id] = (time.monotonic(), served)
    return served


async def is_served(central_hub_project_id: str, accession_id: str) -> bool:
    """Whether the project's approved cohort holds the accession now.

    Raises:
        RuntimeError: If data-access-api cannot answer.
    """
    served = await served_accessions(central_hub_project_id)
    return served is not None and accession_id in served


def _experiments(project_id: str, headers: dict[str, str]) -> list[dict[str, str]]:
    """Lists the experiments the project owns, with their subject.

    The global listing, as in ``get_experiments``: the project's own leaves out sessions of an unregistered modality.

    Raises:
        XnatFetchError: If XNAT does not answer with a listing.
    """
    query = {"format": "json", "project": project_id, "columns": "ID,label,xsiType,subject_ID,project"}
    try:
        response = requests.get(
            xnat_url("data", "experiments", query=query), headers=headers, timeout=XNAT_REQUEST_TIMEOUT
        )
        if response.status_code != 200:
            raise XnatFetchError(f"XNAT experiments fetch failed: {response.status_code}")
        rows = response.json()["ResultSet"]["Result"]
    except (requests.RequestException, ValueError, KeyError, TypeError) as e:
        raise XnatFetchError(f"XNAT experiments fetch failed: {type(e).__name__}") from e
    # An experiment shared into the project belongs to another one, which answers for it.
    return [row for row in rows if row.get("project") == project_id]


def _is_session(experiment: dict[str, str]) -> bool:
    """Whether an experiment is an imaging session; an assessor (a segmentation, say) is not, and goes with its one."""
    return str(experiment.get("xsiType", "")).endswith("SessionData")


def _delete(url: str, headers: dict[str, str]) -> bool:
    """Deletes one XNAT object with its files. The URL names the patient's study, so it is never logged."""
    try:
        response = requests.delete(url, headers=headers, timeout=XNAT_REQUEST_TIMEOUT)
    except requests.RequestException as e:
        logger.warning(f"XNAT delete failed in transport ({type(e).__name__})")
        return False
    if 200 <= response.status_code < 300:
        return True
    logger.warning(f"XNAT delete failed: {response.status_code}")
    return False


def _remove_from_xnat(
    project: Project,
    accessions: list[str],
    sessions: dict[str, dict[str, str]],
    prearchive: dict[str, str],
    headers: dict[str, str],
) -> None:
    """Removes the accessions' sessions from the project's archive and prearchive, then the subjects left empty.

    Args:
        project (Project): The XNAT project.
        accessions (list[str]): The accessions to remove.
        sessions (dict[str, dict[str, str]]): The project's archived imaging sessions, by label.
        prearchive (dict[str, str]): The project's prearchive sessions' paths, by label.
        headers (dict[str, str]): XNAT authentication headers.
    """
    emptied: set[str] = set()
    for accession in accessions:
        removed = accession in prearchive and delete_prearchive_session(prearchive[accession], headers)
        if session := sessions.get(accession):
            segments = ("data", "projects", project.ID, "subjects", session["subject_ID"], "experiments", session["ID"])
            if _delete(xnat_url(*segments, query={"removeFiles": "true"}), headers):
                removed = True
                emptied.add(session["subject_ID"])
        if removed:
            logger.info(
                f"Removed accession {accession} of project {project.secondary_ID} from XNAT: "
                "it left the project's approved cohort"
            )
    if not emptied:
        return
    # Read again: the subject goes only when nothing of theirs is left in the project.
    emptied -= {row.get("subject_ID", "") for row in _experiments(project.ID, headers)}
    for subject_id in emptied:
        url = xnat_url("data", "projects", project.ID, "subjects", subject_id, query={"removeFiles": "true"})
        if _delete(url, headers):
            logger.info(f"Removed a subject of project {project.secondary_ID} from XNAT: none of their studies is left")


async def _reconcile_project(project: Project, headers: dict[str, str], grace_seconds: float, now: float) -> None:
    """Brings one project's cache and XNAT sessions to its served list."""
    hub_id = project.secondary_ID
    try:
        served = await served_accessions(hub_id, max_age=0)
    except RuntimeError:
        logger.warning(f"The approved cohort of project {hub_id} could not be read; its imaging is left as it is")
        return
    if served is None:
        logger.info(f"No accessions are released for project {hub_id}: downloads are refused, nothing is removed")
        return

    # No download can put one back meanwhile: the routes check the list this pass has just read.
    for accession in await run_in_threadpool(remove_accessions_except, BASE_IMAGES_DOWNLOAD_DIR, hub_id, served):
        logger.info(f"Removed the cached copy of accession {accession} of project {hub_id}: it left the cohort")

    experiments = await run_in_threadpool(_experiments, project.ID, headers)
    sessions = {row["label"]: row for row in experiments if _is_session(row)}
    receiving = await run_in_threadpool(get_prearchive_sessions, project.ID, headers)
    prearchive = {name: s.url for s in receiving if (name := s.name or s.folder_name) and s.url}
    left = (sessions.keys() | prearchive.keys()) - served

    for key in [key for key in _left_at if key[0] == project.ID and key[1] not in left]:
        del _left_at[key]  # back on the list, or gone from XNAT
    due = [accession for accession in left if now - _left_at.setdefault((project.ID, accession), now) >= grace_seconds]
    if due:
        await run_in_threadpool(_remove_from_xnat, project, due, sessions, prearchive, headers)


async def reconcile(headers: dict[str, str], grace_seconds: float) -> None:
    """One pass over every XNAT project; a project that fails is left for the next pass.

    Args:
        headers (dict[str, str]): XNAT authentication headers.
        grace_seconds (float): How long a session stays in XNAT after its accession leaves the served list.
    """
    projects = await run_in_threadpool(get_all_projects, headers)
    now = time.monotonic()
    for project in projects:
        try:
            await _reconcile_project(project, headers, grace_seconds, now)
        except Exception as e:
            logger.warning(f"Could not reconcile project {project.secondary_ID} ({type(e).__name__}: {e})")
    live = {project.ID for project in projects}
    for key in [key for key in _left_at if key[0] not in live]:
        del _left_at[key]


async def run_reconcile() -> None:
    """Reconcile every ``COHORT_RECONCILE_MINUTES`` for the app's lifetime."""
    settings = get_settings()
    while True:
        try:
            headers = await run_in_threadpool(get_xnat_auth_headers)
            await reconcile(headers, settings.COHORT_REMOVAL_GRACE_HOURS * 3600)
        except Exception as e:
            logger.warning(f"Approved-cohort reconcile failed ({type(e).__name__}: {e}); trying again later")
        await asyncio.sleep(settings.COHORT_RECONCILE_MINUTES * 60)
