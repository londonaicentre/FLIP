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

import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, col, select

from flip_api.auth.access_manager import authenticate_trust
from flip_api.db.database import get_session
from flip_api.db.models.main_models import Projects, Queries, QueryResult, QueryStats, Trust, TrustTask
from flip_api.domain.schemas.private import (
    AggregatedCohortStats,
    AggregatedFieldResult,
    AggregatedTrustFieldResult,
    FetchedAggregationData,
    OmopCohortResults,
    TrustSpecificData,
)
from flip_api.domain.schemas.status import ProjectStatus, TaskStatus, TaskType
from flip_api.utils.logger import logger

router = APIRouter(tags=["private_services"])


# Whitelist of error categories the trust side may set on a failed cohort task.
# Anything outside this set is collapsed to ``internal_error`` so a future
# code path that accidentally leaks raw psycopg / SQLAlchemy text never
# reaches the cohort UI. The trust-side mapping happens at source in
# trust/data-access-api/data_access_api/services/cohort.py — this is
# belt-and-braces only. Update both lists together.
_ALLOWED_TRUST_ERROR_CATEGORIES: frozenset[str] = frozenset({"query_failed", "query_timeout", "internal_error"})


def _redact_trust_error(raw: str | None) -> str:
    """Collapse a trust-side error string to a safe enum category.

    Args:
        raw (str | None): The error string the trust reported on the failed
            cohort task, if any.

    Returns:
        str: One of ``_ALLOWED_TRUST_ERROR_CATEGORIES``. ``internal_error``
        is the default for anything unrecognised — including ``None``, raw
        psycopg traceback fragments, and wrapped ``"500: ..."`` strings the
        trust-api gateway constructs from upstream HTTPException bodies.
    """
    if not raw:
        return "internal_error"
    # Trust-api wraps upstream HTTPException details as "<status>: <detail>";
    # the trailing token is what data-access-api set as the category.
    candidate = raw.split(":", 1)[-1].strip().lower()
    if candidate in _ALLOWED_TRUST_ERROR_CATEGORIES:
        return candidate
    return "internal_error"


# Task states in which the trust has not been handed the query: PENDING means it has
# not polled it yet, CANCELLED that the hub withdrew it. A result in either state did
# not come from running the dispatched task.
_UNDISPATCHED_TASK_STATUSES: frozenset[TaskStatus] = frozenset({TaskStatus.PENDING, TaskStatus.CANCELLED})

_NOT_DISPATCHED_DETAIL = "Trust is not authorised to submit results for this cohort query"


def _require_dispatched_query(db: Session, trust: Trust, query_id: UUID) -> None:
    """Refuse a result for a query this trust was not dispatched, or whose project is frozen.

    The ``COHORT_QUERY`` ``TrustTask`` row is the authoritative dispatch record: submit creates
    one per trust with the query id, the poll flips it out of PENDING when the trust is handed
    it, and the row is kept (CANCELLED, never deleted) so "was this trust ever asked?" stays
    answerable. ``Queries.queried_trust_ids`` is a denormalised copy for the UI, overwritten on
    every re-submit and carrying no dispatch state, so it is not the gate.

    Args:
        db (Session): Database session.
        trust (Trust): The authenticated trust.
        query_id (UUID): The query the result claims to answer.

    Raises:
        HTTPException: 403 when no dispatched task exists for ``(query_id, trust)`` — one detail
            whether or not the query exists; 404 when a dispatched task exists but the query row
            is gone; 403 when the query's project is no longer UNSTAGED (its cohort of record is
            frozen at staging, the same rule the submit route applies).
    """
    task_statuses = db.exec(
        select(TrustTask.status)
        .where(TrustTask.task_type == TaskType.COHORT_QUERY)
        .where(TrustTask.query_id == query_id)
        .where(TrustTask.trust_id == trust.id)
    ).all()
    if not any(task_status not in _UNDISPATCHED_TASK_STATUSES for task_status in task_statuses):
        logger.warning("Cohort results refused: trust %s was not dispatched query %s.", trust.id, query_id)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_NOT_DISPATCHED_DETAIL)

    row = db.exec(
        select(Queries.id, Projects.status)
        .join(Projects, col(Queries.project_id) == col(Projects.id), isouter=True)
        .where(Queries.id == query_id)
    ).first()
    if row is None:
        logger.warning("Cohort results refused: query %s no longer exists (trust %s).", query_id, trust.id)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cohort query not found.")
    _, project_status = row
    if project_status is not None and project_status != ProjectStatus.UNSTAGED:
        logger.warning(
            "Cohort results refused: project of query %s is %s, not UNSTAGED (trust %s).",
            query_id,
            project_status,
            trust.id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unable to accept cohort results as the project has been staged/approved",
        )


def _save_individual_result(db: Session, cohort_results: OmopCohortResults) -> None:
    """
    Saves the individual cohort results from a single trust for a specific query.

    Args:
        db (Session): Database session.
        cohort_results (OmopCohortResults): The cohort results to save.

    Returns:
        None

    Raises:
        HTTPException: If there is an error while saving the cohort results to the database.
    """
    logger.debug(
        f"Attempting to save individual cohort results for query_id: {cohort_results.query_id}"
        f", trust_id: {cohort_results.trust_id}"
    )

    # Data to be stored in QueryResult.data column (as JSON string)
    data_to_store = json.dumps(
        {
            "record_count": cohort_results.record_count,
            "data": [d.model_dump() for d in cohort_results.data],
            "error": cohort_results.error,
            "suppressed": cohort_results.suppressed,
        }
    )

    # Try to retrieve existing result
    stmt = select(QueryResult).where(
        QueryResult.query_id == cohort_results.query_id, QueryResult.trust_id == cohort_results.trust_id
    )

    try:
        result = db.exec(stmt).first()

        if result:
            # Update existing row
            result.data = data_to_store
            logger.debug(f"Updated existing result for query {cohort_results.query_id}")
        else:
            # Insert new row
            new_result = QueryResult(
                query_id=cohort_results.query_id, trust_id=cohort_results.trust_id, data=data_to_store
            )
            db.add(new_result)
            logger.debug(f"Inserted new result for query {cohort_results.query_id}")

        db.commit()

    except Exception as e:
        db.rollback()
        logger.exception(
            f"Error saving cohort results for query_id {cohort_results.query_id}, trust_id {cohort_results.trust_id}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error saving cohort results: {cohort_results.query_id}",
        ) from e


def _aggregate_and_save_results(db: Session, query_id: UUID) -> None:
    """
    Aggregates cohort results for a specific query_id across all trusts and saves the aggregated statistics.

    Args:
        db (Session): Database session.
        query_id (UUID): The ID of the query to aggregate results for.

    Returns:
        None

    Raises:
        HTTPException: If there is an error during aggregation or saving the aggregated stats to the database.
    """
    logger.debug(f"Starting aggregation for query_id: {query_id}")

    # 1. Fetch all relevant data for the query_id
    # Returns a list of tuples: (trust_name, trust_id, query_result_data)
    statement = (
        select(Trust.name, QueryResult.trust_id, QueryResult.data)
        .join(Trust, col(Trust.id) == col(QueryResult.trust_id))
        .where(QueryResult.query_id == query_id)
        .order_by(col(QueryResult.trust_id))
    )

    try:
        # Serialize aggregation for this query: take a blocking row lock on the parent
        # Queries row before the read-modify-write below, so concurrent trust POSTs
        # aggregate one-at-a-time instead of racing on the single QueryStats row (a stale
        # pass could otherwise clobber a fuller one and drop a responded trust — #579).
        # _save_individual_result has already committed this trust's QueryResult row, so
        # once the lock is held the re-read sees every result posted so far. The lock is
        # released by the commit at the end of this block.
        db.exec(select(Queries).where(Queries.id == query_id).with_for_update()).first()

        rows = db.exec(statement).all()
        logger.debug(f"Response: {rows} for query_id: {query_id}")

        if not rows or not rows[0]:  # Check if any data was returned by json_agg
            logger.warning(
                f"No results found in database for query_id {query_id} during aggregation. Aggregation skipped."
            )
            # Depending on requirements, this might be an error or just a state where no aggregation is done.
            # The original TS code threw an error if response.length was 0.
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,  # Or 404
                detail=f"No results found in database for query_id {query_id} to aggregate.",
            )

        # Manually "aggregate" like json_agg
        trust_names = [row[0] for row in rows]
        trust_ids = [str(row[1]) for row in rows]
        data_json_strs = [row[2] for row in rows]

        fetched_data = FetchedAggregationData(
            trust_name=trust_names,
            trust_id=trust_ids,
            data=data_json_strs,
        )

        # 2. Parse and prepare data
        # QueryResult.data is stored as a JSON string, so we need to parse it as TrustSpecificData objects
        parsed_trust_data_list: list[TrustSpecificData] = []
        for json_str_data in fetched_data.data:
            parsed_trust_data_list.append(TrustSpecificData(**json.loads(json_str_data)))

        # 3. Aggregate
        # Errored trusts don't contribute to record_count or field aggregates —
        # they only populate trust_errors so the UI can show a red chip.
        total_record_count = sum(ptd.record_count for ptd in parsed_trust_data_list if not ptd.error)

        trust_record_counts: dict[str, int] = {}
        trust_errors: dict[str, str] = {}
        trust_suppressed: list[str] = []
        # error and suppressed are mutually exclusive by construction — data-access-api
        # never sets both — so the suppressed flag is only read in the non-errored branch.
        for i, ptd in enumerate(parsed_trust_data_list):
            trust_id_str = fetched_data.trust_id[i]
            if ptd.error:
                # S-8: redact to a safe category before storing. Raw psycopg
                # text could otherwise reach project members via the UI.
                trust_errors[trust_id_str] = _redact_trust_error(ptd.error)
            else:
                trust_record_counts[trust_id_str] = ptd.record_count
                # A suppressed trust still "responded successfully" (count 0), so it
                # belongs in trust_record_counts; trust_suppressed flags it so the UI
                # shows a suppression chip instead of a literal 0 (issue #519).
                if ptd.suppressed:
                    trust_suppressed.append(trust_id_str)

        all_field_names = {d.name for ptd in parsed_trust_data_list if not ptd.error for d in ptd.data or []}

        logger.debug(f"Found field names for aggregation: {all_field_names} for query_id: {query_id}")

        aggregated_field_results: list[AggregatedFieldResult] = []

        if total_record_count > 0:  # Only aggregate if there's data
            for field_name in sorted(all_field_names):  # Sort for consistent output
                current_field_trust_results: list[AggregatedTrustFieldResult] = []
                for i, trust_specific_data_item in enumerate(parsed_trust_data_list):
                    if trust_specific_data_item.error:
                        continue
                    # Only include trusts that contributed to the record count for this field
                    if trust_specific_data_item.record_count > 0 and trust_specific_data_item.data:
                        matching_omop_datum = next(
                            (d for d in trust_specific_data_item.data if d.name == field_name), None
                        )

                        if matching_omop_datum:
                            current_field_trust_results.append(
                                AggregatedTrustFieldResult(
                                    data=matching_omop_datum.results,
                                    trust_name=fetched_data.trust_name[i],
                                    trust_id=fetched_data.trust_id[i],
                                )
                            )
                        else:
                            logger.debug(
                                f"Trust {fetched_data.trust_name[i]} (ID: {fetched_data.trust_id[i]}) did not provide "
                                "data for field '{field_name}' for query_id {query_id}."
                            )

                if current_field_trust_results:  # Only add field if some trusts had data for it
                    aggregated_field_results.append(
                        AggregatedFieldResult(name=field_name, results=current_field_trust_results)
                    )

        final_aggregated_stats = AggregatedCohortStats(
            record_count=total_record_count,
            trusts_results=aggregated_field_results,
            trust_record_counts=trust_record_counts,
            trust_errors=trust_errors,
            trust_suppressed=trust_suppressed,
        )

        # 4. Save aggregated stats
        stats_json = final_aggregated_stats.model_dump_json()
        logger.debug(f"Aggregated stats for query_id {query_id}: {stats_json}")

        # Check if stats already exist for this query_id
        # There should only be one entry per query_id in QueryStats
        existing_stats = db.exec(select(QueryStats).where(QueryStats.query_id == query_id)).first()

        if existing_stats:
            existing_stats.stats = stats_json
            logger.debug(f"Updated existing QueryStats for query_id: {query_id}")
        else:
            new_stats = QueryStats(query_id=query_id, stats=stats_json)
            db.add(new_stats)
            logger.debug(f"Inserted new QueryStats for query_id: {query_id}")

        db.commit()
        logger.info(f"Successfully aggregated and saved stats for query_id: {query_id}")

    except HTTPException:
        db.rollback()
        raise

    except Exception as e:
        db.rollback()
        logger.exception(f"Error during aggregation for query_id {query_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error during aggregation for query_id {query_id}",
        ) from e


# [#114] ✅
@router.post(
    "/cohort/results",
    summary="Receive and process cohort query results from a participating trust.",
    status_code=status.HTTP_200_OK,
    response_model=dict[str, str],
)
def receive_cohort_results_endpoint(
    cohort_results: OmopCohortResults,
    db: Session = Depends(get_session),
    authenticated_trust: Trust = Depends(authenticate_trust),
) -> dict[str, str]:
    """
    Receives cohort query results from a single trust, saves them,
    then re-aggregates and saves the overall statistics for the query.

    Args:
        cohort_results (OmopCohortResults): The cohort results sent by the trust.
        db (Session): Database session.
        authenticated_trust (Trust): Trust resolved from the API key by the dependency.

    Returns:
        dict[str, str]: A message indicating successful processing of cohort results.

    Raises:
        HTTPException: If there is an error during processing, saving individual results, or aggregating results.
    """
    # Verify the authenticated trust owns this result — the payload's trust_id
    # must match the trust the API key resolved to.
    if authenticated_trust.id != cohort_results.trust_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Trust '{authenticated_trust.name}' is not authorised to submit results "
                f"for trust_id {cohort_results.trust_id}"
            ),
        )
    logger.info(f"Received cohort results for query_id: {cohort_results.query_id}, trust_id: {cohort_results.trust_id}")

    # query_id is caller-supplied: only a trust the hub dispatched this query to may write a
    # result under it, and only while the project's cohort is still open. This also guarantees
    # the Queries row the aggregation locks below exists.
    _require_dispatched_query(db, authenticated_trust, cohort_results.query_id)

    try:
        # 1. Save or update the individual result from the trust
        _save_individual_result(db, cohort_results)

        # 2. Trigger aggregation and save the overall stats for the query_id
        # This will re-calculate based on all available query_result entries for this query_id
        _aggregate_and_save_results(db, cohort_results.query_id)

        return {"message": "Cohort results processed successfully"}

    except HTTPException:
        raise

    except Exception as e:
        # This would catch unexpected errors not handled by helpers
        logger.error(
            f"Unhandled error in receive_cohort_results_endpoint for query_id {cohort_results.query_id}: {e}",
            exc_info=True,
        )
        # db.rollback() # Ensure rollback if any commit failed and exception was caught here
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred while processing cohort results for query_id {cohort_results.query_id}.",
        )
