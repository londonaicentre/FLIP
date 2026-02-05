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

import os
from typing import Any
from uuid import UUID

from fastapi import Depends, HTTPException, Path, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from flip_api.auth.access_manager import can_access_model
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.main_models import UploadedFiles
from flip_api.domain.interfaces.model import IModelResponse, IQuery
from flip_api.domain.schemas.status import FileUploadStatus, ModelStatus
from flip_api.utils.logger import logger

RETRIEVE_MODEL_QUERY_FILE = f"{os.path.dirname(os.path.abspath(__file__))}/retrieve_model_query.sql"


def load_sql(file_path: str) -> str:
    """Load an SQL query from a file."""
    with open(file_path, "r") as f:
        return f.read()


def parse_query_from_result(query: Any) -> IQuery:
    """Parse a query from the SQL result into an IQuery object."""
    # TODO review whether to add 'results' field to IQuery
    # It seems to make assumptions that the query returns 'Gender' and 'Age' columns, which may not always be true.
    return IQuery(
        id=query["id"],
        name=query["name"],
        query=query["query"],
    )


def parse_files_from_result(files: Any, model_id: UUID) -> list[UploadedFiles]:
    """Parse files from the SQL result into a list of UploadedFiles objects."""
    parsed_files = [
        UploadedFiles(
            id=f["id"],
            name=f["name"],
            status=FileUploadStatus(f["status"])
            if f["status"] in FileUploadStatus.__members__
            else FileUploadStatus.ERROR,
            size=f["size"],
            type=f["type"],
            tag=f["tag"],
            model_id=model_id,
        )
        for f in files
    ]
    return parsed_files


# [#114] This is not an API endpoint. The endpoint for this is in the 'retrieveModel' step function.
def retrieve_model(
    model_id: UUID = Path(..., title="Model ID"),
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
):
    """
    Retrieve a model by its ID. Returns the model details, including its status, associated files, and query.

    The user must have access to the model to retrieve its details.
    If the model is not found or the user does not have access, an error is returned.

    Args:
        model_id (UUID): The ID of the model to retrieve.
        db (Session): The database session.
        user_id (UUID): The ID of the current user.

    Returns:
        IModelResponse: The details of the model, including its ID, name, description, status, query, and files.

    Raises:
        HTTPException: If the user does not have access to the model or if the model is not found.
        HTTPException: If there is a database error while retrieving the model.
        HTTPException: If an unexpected error occurs during the retrieval process.
    """
    logger.info(f"User {user_id} is requesting model {model_id}")

    try:
        # Check access
        if not can_access_model(user_id, model_id, db):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=f"User {user_id} is denied access to model {model_id}"
            )

        # Uses raw query due to complex joins/aggregates
        query_sql = load_sql(RETRIEVE_MODEL_QUERY_FILE)
        result = (
            db.execute(
                text(query_sql),
                {"model_id": str(model_id)},
            )
            # mappings() tells SQLAlchemy to return each row as a dictionary-like object instead of tuple.
            .mappings()
            # first() retrieves the first result of the query or None if no results were found.
            .first()
        )

        if not result:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Model with ID {model_id} not found")

        # Parse model status from SQL result
        model_status = (
            ModelStatus(result["status"]) if result["status"] in ModelStatus.__members__ else ModelStatus.ERROR
        )

        # Parse query from SQL result
        query = parse_query_from_result(result.get("query")) if result.get("query") else None
        logger.debug(f"Parsed query: {query}")

        # Parse files from SQL result
        files = parse_files_from_result(result.get("files"), model_id) if result.get("files") else []
        logger.debug(f"Parsed files: {files}")

        return IModelResponse(
            model_id=result["model_id"],
            model_name=result["model_name"],
            model_description=result["model_description"],
            project_id=result["project_id"],
            status=model_status,
            query=query,
            files=files,
        )  # type: ignore[call-arg]

    except SQLAlchemyError:
        error_message = "Database error while retrieving model"
        logger.exception(error_message)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error_message)

    except Exception as e:
        error_message = f"An unexpected error occurred while retrieving the model: {str(e)}"
        logger.error(error_message)
        raise HTTPException(
            status_code=e.status_code if hasattr(e, "status_code") else status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_message,
        )
