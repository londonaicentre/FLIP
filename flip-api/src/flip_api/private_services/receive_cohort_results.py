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

from flip_api.auth.access_manager import check_authorization_token
from flip_api.db.database import get_session
from flip_api.db.models.main_models import QueryResult, QueryStats, Trust
from flip_api.domain.schemas.private import (
    AggregatedCohortStats,
    AggregatedFieldResult,
    AggregatedTrustFieldResult,
    FetchedAggregationData,
    OmopCohortResults,
    TrustSpecificData,
)
from flip_api.utils.logger import logger

router = APIRouter(tags=["private_services"])


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
    data_to_store = json.dumps({
        "record_count": cohort_results.record_count,
        "data": [d.model_dump() for d in cohort_results.data],
    })

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
        logger.error(
            f"""
            Exception saving cohort results for query_id {cohort_results.query_id},
            trust_id {cohort_results.trust_id}: {e}
            """,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
            if isinstance(e, HTTPException)
            else f"Error saving cohort results: {cohort_results.query_id}",
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
        total_record_count = sum(ptd.record_count for ptd in parsed_trust_data_list)

        all_field_names: set[str] = set()
        for trust_data_item in parsed_trust_data_list:
            if trust_data_item.data:
                for datum in trust_data_item.data:
                    all_field_names.add(datum.name)

        logger.debug(f"Found field names for aggregation: {all_field_names} for query_id: {query_id}")

        aggregated_field_results: list[AggregatedFieldResult] = []

        if total_record_count > 0:  # Only aggregate if there's data
            for field_name in sorted(list(all_field_names)):  # Sort for consistent output
                current_field_trust_results: list[AggregatedTrustFieldResult] = []
                for i, trust_specific_data_item in enumerate(parsed_trust_data_list):
                    # Only include trusts that contributed to the record count for this field
                    if trust_specific_data_item.record_count > 0 and trust_specific_data_item.data:
                        matching_omop_datum = None
                        for omop_datum in trust_specific_data_item.data:
                            if omop_datum.name == field_name:
                                matching_omop_datum = omop_datum
                                break

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

    except Exception as e:
        db.rollback()
        error_message = f"Error during aggregation for query_id {query_id}: {e}"
        logger.error(error_message)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_message,
        )

    except HTTPException:
        db.rollback()
        raise


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
    token: str = Depends(check_authorization_token),
) -> dict[str, str]:
    """
    Receives cohort query results from a single trust, saves them,
    then re-aggregates and saves the overall statistics for the query.

    Args:
        cohort_results (OmopCohortResults): The cohort results sent by the trust.
        db (Session): Database session.
        token (str): Authorization token (validated by dependency).

    Returns:
        dict[str, str]: A message indicating successful processing of cohort results.

    Raises:
        HTTPException: If there is an error during processing, saving individual results, or aggregating results.
    """
    del token  # Token is validated by the dependency
    logger.info(f"Received cohort results for query_id: {cohort_results.query_id}, trust_id: {cohort_results.trust_id}")

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
