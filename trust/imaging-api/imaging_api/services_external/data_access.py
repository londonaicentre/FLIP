# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

# Interacts with Data Access API

import httpx
from pydantic import BaseModel, Field

from imaging_api.config import get_settings
from imaging_api.utils.exceptions import CohortBelowThresholdError
from imaging_api.utils.logger import logger

DATA_ACCESS_API_URL = get_settings().DATA_ACCESS_API_URL
logger.info(f"Data Access API URL: {DATA_ACCESS_API_URL}")


class AccessionIdsRequest(BaseModel):
    """Request model for the Data Access API to fetch a cohort's accession IDs."""

    encrypted_project_id: str = Field(..., description="The unique identifier for the project")
    query: str = Field(..., description="The raw SQL query to execute")


# C-FIND matching characters that turn one accession number into a pattern: `*` and `?` are the
# DICOM wildcards for string keys, `\\` separates the values of a multi-valued key (list matching).
_MULTI_STUDY_CHARACTERS = frozenset("*?\\")


def _is_single_study_accession(value: object) -> bool:
    """Whether ``value`` is an accession number that names at most one study to a PACS."""
    return isinstance(value, str) and bool(value.strip()) and not (_MULTI_STUDY_CHARACTERS & set(value))


async def get_accession_ids(encrypted_project_id: str, query: str) -> list[str]:
    """
    Calls the data-access-api ``/cohort/accession-ids`` endpoint and returns the
    list of accession IDs for the cohort.

    The endpoint projects the cohort query to the ``accession_id`` column
    server-side, so no other patient attributes leave the trust's data store.

    Args:
        encrypted_project_id (str): The encrypted project ID.
        query (str): The SQL query to execute.

    A cohort query may return several rows per study — one ``image_occurrence`` per series, say — and
    an accession is one study, imported once: the ids are de-duplicated here, first occurrence wins,
    query order kept, so the same accession is neither queried and queued once per row nor counted
    once per row in the import status (FLIP#1123). Ids that could match more than one study — blank,
    or carrying a C-FIND wildcard or value delimiter — are dropped with a warning, never sent.

    Returns:
        list[str]: The distinct, single-study accession IDs returned by the cohort query, in query order.

    Raises:
        CohortBelowThresholdError: If the cohort is smaller than the trust's
            ``COHORT_QUERY_THRESHOLD`` and data-access-api refuses to release identifiers.
        RuntimeError: If the HTTP call to the Data Access API fails for any other reason
            (network error or other non-2xx response).
    """
    request = AccessionIdsRequest(encrypted_project_id=encrypted_project_id, query=query)

    logger.debug(f"get_accession_ids: Sending request to Data Access API with {encrypted_project_id=}")

    headers = {
        get_settings().TRUST_INTERNAL_SERVICE_KEY_HEADER: get_settings().TRUST_INTERNAL_SERVICE_KEY,
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{DATA_ACCESS_API_URL}/cohort/accession-ids",
                json=request.model_dump(),
                headers=headers,
            )

        response.raise_for_status()
        rows = list(response.json().get("accession_ids", []))
        # An accession that can match more than one study must never reach the PACS. The value goes
        # into a C-FIND key, and to a PACS a zero-length key is universal matching (DQR answered with
        # 500 studies, the page cap, on the dev PACS), `*` and `?` are wildcards, and `\\` is DICOM's
        # value delimiter (list matching: two studies for "A\\B") — with the import taking the first
        # study an answer returns, any of them would attach a study outside the cohort to the
        # project. None occurs in a real accession number (`\\` is illegal in the SH VR; `*` and `?`
        # are reserved for matching), so such a value is a defect in the trust's OMOP, not a cohort
        # with less imaging (image_occurrence.accession_id is required for every imaging row; see the
        # OMOP component docs), and the row is dropped and counted here rather than sent.
        usable = [value for value in rows if _is_single_study_accession(value)]
        if len(usable) != len(rows):
            logger.warning(
                f"get_accession_ids: dropping {len(rows) - len(usable)} accession id(s) that are blank or carry a "
                "C-FIND wildcard (*, ?) or value delimiter (\\) — each would match more than one study in the PACS; "
                "every image_occurrence row in the cohort must carry exactly one study's accession number"
            )
        accession_ids = list(dict.fromkeys(usable))
        if len(accession_ids) != len(usable):
            logger.info(
                f"get_accession_ids: {len(usable)} cohort rows collapse to {len(accession_ids)} distinct accession(s)"
            )
        return accession_ids

    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == httpx.codes.FORBIDDEN:
            # A deliberate refusal, not a failure — typed separately so callers can report it
            # as a settled outcome rather than as a transport error they might retry. Two
            # policy refusals arrive as 403: the frozen cohort is below the trust's disclosure
            # threshold, or the project has no approved-cohort snapshot yet (FLIP#857 —
            # e.g. imaging creation raced ahead of the snapshot task). Both mean "no
            # identifiers releasable right now"; relay data-access-api's own detail so the
            # two stay distinguishable in status reporting.
            try:
                detail = exc.response.json().get("detail", "")
            except ValueError:
                detail = ""
            message = f"get_accession_ids: the Data Access API refused to release accession IDs — {detail}"
            logger.warning(message)
            raise CohortBelowThresholdError(message) from exc
        error_message = f"get_accession_ids: HTTP error occurred while calling the Data Access API: {exc}"
        logger.error(error_message)
        raise RuntimeError(error_message) from exc

    except httpx.HTTPError as exc:
        error_message = f"get_accession_ids: HTTP error occurred while calling the Data Access API: {exc}"
        logger.error(error_message)
        raise RuntimeError(error_message) from exc
