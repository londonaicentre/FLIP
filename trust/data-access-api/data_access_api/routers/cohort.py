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
    SnapshotDeleteRequest,
    SnapshotResponse,
    StatisticsResponse,
)
from data_access_api.services.cohort import get_records, get_statistics, validate_query
from data_access_api.services.cohort_snapshot import (
    Snapshot,
    SnapshotTooLarge,
    delete_snapshot,
    get_snapshot,
    normalised_query_hash,
    save_snapshot,
    snapshot_enabled,
)
from data_access_api.utils.encryption import decrypt
from data_access_api.utils.internal_auth import authenticate_internal_service
from data_access_api.utils.logger import logger

# Returned instead of row-level data when a cohort is smaller than
# COHORT_QUERY_THRESHOLD, by both row-level routes (/cohort/dataframe and
# /cohort/accession-ids). Deliberately fixed text: it must be identical for a
# cohort of zero and a cohort of threshold-minus-one, or the refusal itself
# becomes a one-row oracle for probing the database.
_BELOW_THRESHOLD_DETAIL = "Cohort is too small for row-level data to be released."

# Returned by the row-level routes for a project with no frozen cohort artefact.
# Row-level data is released ONLY from the snapshot persisted at approval
# (FLIP#857); there is no live-SQL serving path. Deliberately generic: it must
# not reveal whether the project exists.
_NO_SNAPSHOT_DETAIL = "No approved cohort snapshot exists for this project."


# Create Router
router = APIRouter(prefix="/cohort", tags=["Cohort"], dependencies=[Depends(authenticate_internal_service)])


def _require_snapshot(project_id: str) -> Snapshot:
    """The project's frozen cohort, or the fixed 403 when there is none.

    The snapshot persisted at approval is the ONLY source of row-level data: a project
    that was never approved (or whose snapshot was purged, or whose trust has the store
    unconfigured/unwritable) is refused. Fail-closed by construction — there is no
    live-SQL fallback for these routes.
    """
    snapshot = get_snapshot(project_id)
    if snapshot is None:
        logger.warning(f"Refusing row-level data for project {project_id}: no approved cohort snapshot")
        raise HTTPException(status_code=403, detail=_NO_SNAPSHOT_DETAIL)
    return snapshot


def _check_frozen_threshold(project_id: str, snapshot: Snapshot) -> None:
    """Apply ``COHORT_QUERY_THRESHOLD`` to the FROZEN row count.

    The gate reads the snapshot, not live OMOP: the frozen cohort is immutable, so a project
    that cleared the threshold at approval keeps serving even while the live database drifts
    underneath (pre-FLIP#857, the SQL was re-run on every call and the answer could change).
    The refusal reuses the fixed below-threshold text so a zero-row snapshot and a
    below-threshold one stay indistinguishable. The threshold itself is still read live, so
    an operator RAISING their disclosure floor takes effect on already-approved projects.
    """
    if snapshot.meta.row_count < get_settings().COHORT_QUERY_THRESHOLD:
        logger.warning(
            f"Withholding frozen cohort for project {project_id}: snapshot of "
            f"{snapshot.meta.row_count} rows is below the minimum size of "
            f"{get_settings().COHORT_QUERY_THRESHOLD}"
        )
        raise HTTPException(status_code=403, detail=_BELOW_THRESHOLD_DETAIL)


def _log_ignored_client_query(project_id: str, snapshot: Snapshot, client_query: str) -> None:
    """Record that the caller-supplied SQL was ignored in favour of the frozen cohort.

    Serving the snapshot regardless of the submitted SQL is what closes the arbitrary-SQL
    exposure on these routes (see FLIP#857's audit note): the only row-level data obtainable
    under a project's id is the cohort that was approved. The hash comparison exists purely
    so a mismatch is visible in the trust's logs; the ``query`` field stays in the request
    schema because the FL client library sends it.
    """
    if normalised_query_hash(client_query) != snapshot.meta.query_hash:
        logger.warning(
            f"Client-supplied query for project {project_id} differs from the frozen cohort "
            "query — ignored; serving the approved snapshot."
        )


@router.post("", response_model=StatisticsResponse)
def receive_cohort_query(query_input: CohortQueryInput) -> StatisticsResponse:
    """
    Receives a cohort query and returns the aggregated statistics.

    This is the one route that always evaluates LIVE OMOP: it runs pre-approval by
    definition (it is how a proposed cohort is sized in the first place), and it
    releases aggregates only.

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
    Serves the project's FROZEN cohort in a DataFrame-like structure (column-oriented dict).

    This is the training-data path: user-supplied FL code inside the trust's fl-client
    reaches it through ``flip.get_dataframe(...)``, so it necessarily returns row-level
    records — a model trains on rows. The data stays inside the trust; only model updates
    leave it.

    What it serves is the cohort snapshot persisted at project approval (FLIP#857), keyed
    on the project id — **the caller-supplied SQL is ignored** (logged when it differs from
    the frozen query). Consequences, all deliberate: the cohort cannot grow after approval;
    every training round fetches an identical frame; and researcher code cannot execute
    arbitrary SQL under an approved project's id. A project with no snapshot is refused —
    there is no live-SQL serving path on this route.

    Because it is row-level, the frozen cohort must clear ``COHORT_QUERY_THRESHOLD``
    before anything is released, mirroring the suppression the ``/cohort`` statistics
    route applies. The threshold is read live, so an operator raising their disclosure
    floor takes effect on already-approved projects.

    Args:
        query_input (DataframeQuery): The encrypted project id (and the advisory query).

    Returns:
        dict[str, list[Any]]: The frozen cohort in a DataFrame-like structure.

    Raises:
        HTTPException: 403 if the project has no cohort snapshot or the frozen cohort is
            below the disclosure threshold.
    """
    project_id = decrypt(query_input.encrypted_project_id)

    logger.info(f"Received DataFrame query for project {project_id}")

    snapshot = _require_snapshot(project_id)
    _log_ignored_client_query(project_id, snapshot, query_input.query)
    _check_frozen_threshold(project_id, snapshot)
    logger.info(f"Serving frozen cohort snapshot for project {project_id}: {snapshot.meta.row_count} rows")
    return snapshot.df.to_dict(orient="list")


@router.post("/accession-ids", response_model=AccessionIdsResponse)
def get_accession_ids(query_input: DataframeQuery) -> AccessionIdsResponse:
    """
    Returns only the ``accession_id`` column of the project's FROZEN cohort.

    This is the minimal-disclosure endpoint used by imaging-api to fetch the accession
    numbers it needs to import studies from PACS — it does not expose row-level patient
    attributes. Like ``/cohort/dataframe`` it serves the snapshot persisted at approval and
    **ignores the caller-supplied SQL** (FLIP#857): the imaging status poll (roughly every
    10 s while a project page is open) and reimport read a stable pointer set instead of
    re-running the cohort SQL against a live OMOP that changes underneath.

    Accession IDs are still row-level identifiers, and they are the pointer set into
    the imaging data: they decide whose studies get pulled into XNAT, where project
    members view them. So the frozen cohort must clear ``COHORT_QUERY_THRESHOLD`` here
    just as it must on ``/cohort/dataframe``, and the refusal reuses that route's fixed
    text so a zero-row cohort and a below-threshold one are indistinguishable. The trust
    applies this itself rather than relying on the hub's staging guard — the hub is a
    separate administrative domain, and a trust must stay safe regardless of what the hub
    checked.

    A frozen cohort with no ``accession_id`` column returns an EMPTY list rather than an
    error: a tabular/OMOP-only project legitimately has no imaging to pull.

    Args:
        query_input (DataframeQuery): The encrypted project id (and the advisory query).

    Returns:
        AccessionIdsResponse: The frozen cohort's accession IDs.

    Raises:
        HTTPException: 403 if the project has no cohort snapshot or the frozen cohort is
            below the disclosure threshold.
    """
    project_id = decrypt(query_input.encrypted_project_id)

    logger.info(f"Received accession-ids query for project {project_id}")

    snapshot = _require_snapshot(project_id)
    _log_ignored_client_query(project_id, snapshot, query_input.query)
    # Threshold before the column check: nothing about the cohort's shape is revealed for a
    # below-threshold snapshot.
    _check_frozen_threshold(project_id, snapshot)
    if not snapshot.meta.has_accessions:
        logger.info(f"Frozen cohort for project {project_id} has no accession_id column (tabular project)")
        return AccessionIdsResponse(accession_ids=[])
    frozen_ids = [str(value) for value in snapshot.df["accession_id"].tolist()]
    logger.info(f"Serving {len(frozen_ids)} frozen accession ids for project {project_id}")
    return AccessionIdsResponse(accession_ids=frozen_ids)


@router.post("/snapshot", response_model=SnapshotResponse)
def create_snapshot(query_input: DataframeQuery) -> SnapshotResponse:
    """
    Materialises the cohort ONCE and persists it as this project's frozen artefact (FLIP#857).

    Called by trust-api when the hub approves a project. From that point the two row-level
    routes serve the persisted dataframe keyed on the project id and ignore caller SQL, so
    the cohort a project trains on is exactly the cohort that was approved — it cannot grow
    with the live database, and it is identical on every fetch. Re-approval calls this again
    and atomically replaces the artefact (which is also how OMOP-side removals and opt-outs
    propagate into an approved project: at explicit re-snapshot events, never silently
    mid-training).

    This route (with the statistics route) is where live OMOP is evaluated, exactly once
    per approval; ``validate_query`` remains the authority on the SQL executed here. The
    disclosure threshold is enforced BEFORE anything is persisted: a below-threshold cohort
    leaves no artefact and returns the same fixed refusal as the row-level routes. The
    response carries aggregates only (count, column names, timestamps) — the row-level data
    stays on this trust's disk.

    Args:
        query_input (DataframeQuery): The approved cohort query and encrypted project id.

    Returns:
        SnapshotResponse: What was frozen.

    Raises:
        HTTPException: 400 if the query is invalid or the project id is not a UUID, 403 if
            the cohort is below the disclosure threshold, 413 if the serialized snapshot
            exceeds ``SNAPSHOT_MAX_BYTES``, 500 if the query fails to execute, 503 if the
            snapshot store is not configured.
    """
    if not snapshot_enabled():
        raise HTTPException(status_code=503, detail="Cohort snapshot store is not configured on this trust.")

    project_id = decrypt(query_input.encrypted_project_id)
    logger.info(f"Received cohort snapshot request for project {project_id}")

    safe_query = validate_query(query_input.query)
    try:
        df = get_records(safe_query)
    except HTTPException:
        # get_records already converts driver errors into category-only
        # HTTPExceptions; re-wrapping them below would discard that work and
        # turn every categorised 400 into an opaque 500.
        raise
    except SQLAlchemyError:
        logger.exception("Snapshot cohort query failed with a database error")
        raise HTTPException(status_code=500, detail="Query execution failed.")
    except Exception:
        # Detail is category-only, as on the other routes: it travels back to the hub.
        logger.exception("Snapshot cohort query failed unexpectedly")
        raise HTTPException(status_code=500, detail="Query execution failed.")

    if len(df) < get_settings().COHORT_QUERY_THRESHOLD:
        logger.warning(
            f"Refusing to snapshot project {project_id}: cohort below the minimum size of "
            f"{get_settings().COHORT_QUERY_THRESHOLD}"
        )
        raise HTTPException(status_code=403, detail=_BELOW_THRESHOLD_DETAIL)

    try:
        # The hash is of the RAW submitted SQL (not the validator's re-emission) so serving
        # can compare it against the raw query the hub injects into FL job configs.
        meta = save_snapshot(project_id, df, query_hash=normalised_query_hash(query_input.query))
    except ValueError:
        raise HTTPException(status_code=400, detail="Project id is not a valid UUID.")
    except SnapshotTooLarge as err:
        logger.error(str(err))
        raise HTTPException(status_code=413, detail="Cohort snapshot exceeds the configured size limit.")
    except OSError:
        logger.exception("Cohort snapshot store write failed")
        raise HTTPException(status_code=500, detail="Snapshot persistence failed.")

    return SnapshotResponse(
        row_count=meta.row_count,
        columns=meta.columns,
        has_accessions=meta.has_accessions,
        snapshot_at=meta.created_at,
        query_hash=meta.query_hash,
    )


@router.post("/snapshot/delete")
def remove_snapshot(query_input: SnapshotDeleteRequest) -> dict[str, bool]:
    """
    Removes a project's frozen cohort artefact. Idempotent.

    The teardown hook for the project purge path (FLIP#997 — which has no hub-side caller
    yet, so nothing invokes this in the current lifecycle). After deletion the project's
    row-level routes refuse until a re-approval creates a fresh snapshot.

    Args:
        query_input (SnapshotDeleteRequest): The encrypted project id.

    Returns:
        dict[str, bool]: ``{"deleted": bool}`` — False when no snapshot existed.
    """
    project_id = decrypt(query_input.encrypted_project_id)
    deleted = delete_snapshot(project_id)
    logger.info(f"Snapshot delete for project {project_id}: {'removed' if deleted else 'nothing to remove'}")
    return {"deleted": deleted}
