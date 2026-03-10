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

from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlmodel import Session

from flip_api.auth.access_manager import can_access_model
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.model_services.services.model_service import get_model_status
from flip_api.utils.get_secrets import get_secret
from flip_api.utils.logger import logger

router = APIRouter(prefix="/model", tags=["model_services"])


# [#114] ✅
# TODO Review if this endpoint is useful once we implement logging -- not sure where logs will live
@router.get("/{model_id}/training/log", status_code=status.HTTP_200_OK, response_model=dict[str, str])
def retrieve_model_status_from_logs(
    model_id: UUID,
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> dict[str, str]:
    """
    Determine the most recent status of a federated learning model based on logs stored in an Elasticsearch index and
    persist relevant log entries into a PostgreSQL fl_logs table.
    This function retrieves logs from Elasticsearch, checks the status of the model, and inserts log entries into the
    PostgreSQL database if they do not already exist.

    Args:
        model_id (UUID): The ID of the model to retrieve logs for.
        db (Session): The database session.
        user_id (UUID): The ID of the user making the request.

    Returns:
        dict[str, str]: A dictionary containing the latest status of the model.

    Raises:
        HTTPException: If the user does not have access to the model, if the model does not exist, or if there are
                       issues retrieving logs from Elasticsearch.
    """
    # Authorization
    if not can_access_model(user_id, model_id, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to this model.")

    # Model validation
    model_status = get_model_status(model_id, db)
    if not model_status or model_status.deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Model ID: {model_id} does not exist.")

    # Prepare Elastic query
    query_body = {
        "size": 10000,
        "query": {"match_phrase": {"model": str(model_id)}},
        "sort": [{"@timestamp": {"order": "desc"}}],
    }

    elastic_url = get_secret("elastic-search-url")
    if not elastic_url:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Elasticsearch URL not found.")

    try:
        response = httpx.post(f"{elastic_url}/centralhub-eks/_search", json=query_body)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            logger.debug("Could not find model status from logs.")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Logs not found.")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))

    logs = response.json().get("hits", {}).get("hits", [])
    if not logs:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No logs found.")

    values = []
    params = []
    counter = 0
    latest_status = None

    for log in logs:
        source = log.get("_source", {})
        ts = source.get("@timestamp")
        status_in_log = source.get("status")
        message = source.get("message")
        trust = source.get("trust")

        if not ts or not status_in_log or not source.get("model"):
            continue

        # Determine status
        if (status_in_log.endswith("_COMPLETED") and not trust and not latest_status) or (
            status_in_log and not latest_status
        ):
            latest_status = {"modelStatus": status_in_log}

        if message == "returned status: dead" or message.startswith("trust exception: "):
            latest_status = {"modelStatus": "ERROR"}

        # Prepare insert values
        values.append(f"(:model{counter}, :date{counter}, :success{counter}, :trust{counter}, :log{counter})")
        params.append({
            f"model{counter}": source["model"],
            f"date{counter}": ts,
            f"success{counter}": message != "returned status: dead",
            f"trust{counter}": trust or "",
            f"log{counter}": message,
        })
        counter += 1

    if values:
        flat_params = {k: v for d in params for k, v in d.items()}
        insert_stmt = f"""
            INSERT INTO fl_logs (model_id, logdate, success, trust_name, log)
            VALUES {", ".join(values)}
            ON CONFLICT DO NOTHING
        """
        db.execute(text(insert_stmt), flat_params)
        logger.info(f"Inserted {len(values)} log entries for model {model_id}.")

    if latest_status:
        return latest_status

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No status found in logs.")
