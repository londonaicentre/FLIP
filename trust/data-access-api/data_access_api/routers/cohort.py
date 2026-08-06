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

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from data_access_api.config import get_settings
from data_access_api.routers.schema import (
    AccessionIdsResponse,
    CohortQueryInput,
    DataframeQuery,
    StatisticsResponse,
)
from data_access_api.services.cohort import get_records, get_statistics, validate_query
from data_access_api.utils.encryption import decrypt
from data_access_api.utils.internal_auth import authenticate_internal_service
from data_access_api.utils.logger import logger

# Returned instead of row-level data when a cohort is smaller than
# COHORT_QUERY_THRESHOLD, by both row-level routes (/cohort/dataframe and
# /cohort/accession-ids). Deliberately fixed text: it must be identical for a
# cohort of zero and a cohort of threshold-minus-one, or the refusal itself
# becomes a one-row oracle for probing the database.
_BELOW_THRESHOLD_DETAIL = "Cohort is too small for row-level data to be released."


# Create Router
router = APIRouter(prefix="/cohort", tags=["Cohort"], dependencies=[Depends(authenticate_internal_service)])


@router.post("", response_model=StatisticsResponse)
def receive_cohort_query(query_input: CohortQueryInput) -> StatisticsResponse:
    """
    Receives a cohort query and returns the aggregated statistics.

    Below-threshold results are privacy-suppressed: any count below the threshold —
    including a genuine zero — comes back as a normal ``StatisticsResponse`` with
    ``record_count=0``, empty ``data`` and ``suppressed=True``, *not* an HTTP error.
    Returning an error here caused trust-api to skip reporting back to the hub, which left
    the per-trust UI status stuck on "running". A true zero and a small below-threshold
    count are deliberately indistinguishable so the response can't reveal that >=1 patient
    matched; the ``suppressed`` flag only tells the hub/UI to show a "below-threshold" chip
    rather than a bare 0 (issue #519).

    Args:
        query_input (data_access_api.routers.schema.CohortQueryInput): The input data for the cohort query.

    Returns:
        StatisticsResponse: The aggregated statistics from the query results, or a 0-count response
        when the count is below ``COHORT_QUERY_THRESHOLD``.

    Raises:
        HTTPException: If there is an error during the execution of the query.
    """
    logger.info("Received cohort query")

    minimum_cohort_size = get_settings().COHORT_QUERY_THRESHOLD
    logger.info(f"Minimum cohort size needed to return statistics: {minimum_cohort_size}")

    # On the original implementation get_records was invoked within get_statistics. However, to better handle
    # exceptions and log the query execution, we separate the two calls here.
    safe_query = validate_query(query_input.query)

    try:
        logger.info("Executing cohort query")

        df = get_records(safe_query)
        df = df.dropna(axis=1, how="all")  # Ignore entirely empty columns
        # drop duplicate columns
        df = df.loc[:, ~df.columns.duplicated()]
    except Exception as e:
        logger.error(f"Error executing query: {str(e)}")
        raise e

    try:
        results = get_statistics(df, query_input=query_input, threshold=minimum_cohort_size)
    except Exception:
        # Detail is category-only: the trust forwards it to the hub, which shows it to every
        # project member, and raw exception text from the aggregation can carry row values.
        logger.exception("Cohort statistics aggregation failed")
        raise HTTPException(status_code=500, detail="Statistics aggregation failed.")

    logger.info("Cohort query returned results")
    return results


@router.post("/dataframe")
def get_dataframe(query_input: DataframeQuery) -> dict[str, list[Any]]:
    """
    Retrieves query results in a DataFrame-like structure (column-oriented dictionary).

    This is the training-data path: user-supplied FL code inside the trust's
    fl-client reaches it through ``flip.get_dataframe(...)``, so it necessarily
    returns row-level records — a model trains on rows. The data stays inside
    the trust; only model updates leave it.

    Because it is row-level, the cohort must clear ``COHORT_QUERY_THRESHOLD``
    before anything is released, mirroring the suppression the ``/cohort``
    statistics route already applies. A cohort below the threshold is refused
    outright rather than returned truncated: there is no training value in it,
    and releasing a handful of identifiable rows is exactly the disclosure the
    threshold exists to prevent.

    Note there is deliberately no column allowlist here. ``accession_id`` is
    load-bearing — it is how the returned rows join to the imaging studies
    pulled into XNAT — and shipped tutorials legitimately select ``*``, so a
    column filter would break every FL app on the platform while a caller could
    trivially alias around it. Column-level minimisation belongs in the cohort
    query the project submits and in project approval, not here.

    Args:
        query_input (DataframeQuery): The input data for the DataFrame query.

    Returns:
        dict[str, list[Any]]: The query results in a DataFrame-like structure.

    Raises:
        HTTPException: 400 if the query is invalid, 403 if the cohort is below
            the disclosure threshold, 500 if the query fails to execute.
    """
    project_id = decrypt(query_input.encrypted_project_id)

    logger.info(f"Received DataFrame query for project {project_id}")

    safe_query = validate_query(query_input.query)

    try:
        df = get_records(safe_query)
    except HTTPException:
        # get_records already converts driver errors into category-only
        # HTTPExceptions; re-wrapping them below would discard that work and
        # turn every categorised 400 into an opaque 500.
        raise
    except SQLAlchemyError:
        logger.exception("DataFrame query failed with a database error")
        raise HTTPException(status_code=500, detail="Query execution failed.")
    except Exception:
        # Detail is category-only: the trust forwards it to the hub, which shows
        # it to every project member, and raw exception text can carry row
        # values and connection internals.
        logger.exception("DataFrame query failed unexpectedly")
        raise HTTPException(status_code=500, detail="Query execution failed.")

    minimum_cohort_size = get_settings().COHORT_QUERY_THRESHOLD
    if len(df) < minimum_cohort_size:
        logger.warning(
            f"Withholding row-level data for project {project_id}: "
            f"cohort below the minimum size of {minimum_cohort_size}"
        )
        raise HTTPException(status_code=403, detail=_BELOW_THRESHOLD_DETAIL)

    return df.to_dict(orient="list")


@router.post("/accession-ids", response_model=AccessionIdsResponse)
def get_accession_ids(query_input: DataframeQuery) -> AccessionIdsResponse:
    """
    Returns only the ``accession_id`` column of the cohort, projected server-side.

    The caller's query is wrapped as ``SELECT accession_id FROM (<query>) sub`` so
    no other columns ever cross the trust boundary. This is the minimal-disclosure
    endpoint used by imaging-api to fetch the accession numbers it needs to import
    studies from PACS — it does not expose row-level patient attributes.

    Accession IDs are still row-level identifiers, and they are the pointer set into
    the imaging data: they decide whose studies get pulled into XNAT, where project
    members view them. So the cohort must clear ``COHORT_QUERY_THRESHOLD`` here just
    as it must on ``/cohort/dataframe``, and the refusal reuses that route's fixed
    text so a zero-row cohort and a below-threshold one are indistinguishable.

    The trust applies this itself rather than relying on the hub's staging guard
    (``flip_api.project_services.stage_project``, which refuses to stage a trust whose
    cohort came back empty or suppressed). The hub is a separate administrative domain;
    a trust must stay safe regardless of what the hub checked, and the hub's own
    ``start_project_imaging_creation`` endpoint does not re-check staging.

    **This is evaluated against the live cohort on every call, not once at approval.**
    The endpoint is re-invoked by the imaging status poll roughly every 10 s while a
    user has the project page open, and again on reimport — each time re-running the
    cohort SQL against OMOP, which changes underneath. A project can therefore pull
    cleanly at approval and later start refusing if its cohort shrinks below the
    threshold. FLIP has no frozen approved-cohort artefact; see FLIP#857.

    Args:
        query_input (DataframeQuery): The cohort query.

    Returns:
        AccessionIdsResponse: The accession IDs returned by the cohort query.

    Raises:
        HTTPException: 400 if the query is invalid or does not select an
            ``accession_id`` column, 403 if the cohort is below the disclosure
            threshold, 500 if the query fails to execute.
    """
    project_id = decrypt(query_input.encrypted_project_id)

    logger.info(f"Received accession-ids query for project {project_id}")

    # validate_query returns the caller's SQL re-emitted from its parsed AST,
    # which breaks any injection taint chain and strips trailing semicolons so
    # the inner query composes cleanly inside the outer SELECT subquery.
    safe_inner = validate_query(query_input.query)
    wrapped_query = f"SELECT accession_id FROM ({safe_inner}) AS cohort_subquery"

    try:
        df = get_records(wrapped_query)
    except HTTPException:
        # get_records already converts driver errors into category-only
        # HTTPExceptions; re-wrapping them below would discard that work and
        # turn every categorised 400 into an opaque 500.
        raise
    except SQLAlchemyError:
        logger.exception("Accession-ids query failed with a database error")
        raise HTTPException(status_code=500, detail="Query execution failed.")
    except Exception:
        # Detail is category-only: the trust forwards it to the hub, which shows
        # it to every project member, and raw exception text can carry row
        # values and connection internals.
        logger.exception("Accession-ids query failed unexpectedly")
        raise HTTPException(status_code=500, detail="Query execution failed.")

    minimum_cohort_size = get_settings().COHORT_QUERY_THRESHOLD
    if len(df) < minimum_cohort_size:
        logger.warning(
            f"Withholding accession IDs for project {project_id}: "
            f"cohort below the minimum size of {minimum_cohort_size}"
        )
        raise HTTPException(status_code=403, detail=_BELOW_THRESHOLD_DETAIL)

    # No "did the DataFrame come back with accession_id?" guard here: it could never fire.
    # The wrapper above selects the column explicitly, so a cohort that does not project it
    # fails inside get_records with UndefinedColumn — surfacing as a category 400 through the
    # `except HTTPException: raise` branch, before any DataFrame exists. Pinned by
    # tests/integration/test_cohort_endpoint.py::test_accession_ids_missing_column_surfaces_get_records_400.
    accession_ids = [str(value) for value in df["accession_id"].tolist()]
    logger.info(f"accession-ids query for project {project_id} returned {len(accession_ids)} ids")
    return AccessionIdsResponse(accession_ids=accession_ids)
