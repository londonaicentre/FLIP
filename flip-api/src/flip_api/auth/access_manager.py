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

from fastapi import HTTPException, Security, status
from fastapi.security.api_key import APIKeyHeader
from sqlmodel import Session, select

from flip_api.auth.auth_utils import has_permissions
from flip.config import get_settings
from flip.db.models.main_models import Model, Projects, ProjectUserAccess, Queries
from flip.db.models.user_models import PermissionRef
from flip.utils.logger import logger


def can_access_project(user_id: UUID, project_id: UUID, db: Session) -> bool:
    """
    Check if a user has access to a specific project.

    Args:
        user_id: ID of the user
        project_id: ID of the project
        db: Database session

    Returns:
        True if the user has access to the project, False otherwise
    """
    logger.debug(f"Checking if user: {user_id} can access project: {project_id}")

    # First check if user has CAN_MANAGE_PROJECTS permission
    if has_permissions(user_id, [PermissionRef.CAN_MANAGE_PROJECTS], db):
        logger.debug(f"User: {user_id} has the {PermissionRef.CAN_MANAGE_PROJECTS} permission and is granted access.")
        return True

    try:
        # Check if user is project owner or has explicit access
        """
            SELECT COUNT(projects.id)::int
            FROM projects
            LEFT JOIN project_user_access
                ON project_user_access.project_id = projects.id
            WHERE (projects.owner_id = :userId
                OR project_user_access.userid = :userId)
            AND projects.id = :project_id
        """
        query = (
            select(Projects.id)
            .where((Projects.owner_id == user_id) | (ProjectUserAccess.user_id == user_id))
            .where(Projects.id == project_id)
        )
        result = db.exec(query)
        count = result.first()

        if not count:
            logger.debug(f"User: {user_id} is neither the project owner or an approved user and is not granted access.")
            return False

        logger.debug(f"User: {user_id} is either the project owner or an approved user and is granted access.")
        return True

    except Exception as e:
        logger.error(f"Error checking project access for user {user_id}, project {project_id}: {str(e)}")
        return False


def can_access_model(user_id: UUID, model_id: UUID, db: Session) -> bool:
    """
    Check if a user has access to a specific model.

    Args:
        user_id (UUID): ID of the user
        model_id (UUID): ID of the model
        db (Session): Database session

    Returns:
        bool: True if the user has access to the model, False otherwise

    Raises:
        HTTPException: If there is an error during the access check
    """
    logger.debug(f"Checking if user: {user_id} can access model: {model_id}")

    # First check if user has CAN_MANAGE_PROJECTS permission
    if has_permissions(user_id, [PermissionRef.CAN_MANAGE_PROJECTS], db):
        logger.debug(f"User: {user_id} has the {PermissionRef.CAN_MANAGE_PROJECTS} permission and is granted access.")
        return True

    try:
        # Check if user is project owner or has explicit access
        """
            SELECT COUNT(projects.id)::int as count
            FROM projects
            INNER JOIN model
                ON model.project_id = projects.id AND model.id = :model_id
            LEFT JOIN project_user_access
                ON project_user_access.project_id = projects.id
            WHERE (projects.owner_id = :user_id
                OR project_user_access.userid = :user_id)
        """
        query = (
            select(Projects.id)
            .join(Model)
            .outerjoin(ProjectUserAccess)
            .where((Projects.owner_id == user_id) | (ProjectUserAccess.user_id == user_id))
            .where((Model.id == model_id) & (Model.project_id == Projects.id))
        )
        result = db.exec(query)
        results = result.all()
        logger.debug(f"Query result for user {user_id} and model {model_id}: {results}")
        first_result = results[0] if results else None
        logger.debug(f"Results first: {first_result}")

        if not first_result:
            logger.debug(f"User: {user_id} is neither the project owner or an approved user and is not granted access.")
            return False

        logger.debug(f"User: {user_id} is either the project owner or an approved user and is granted access.")
        return True

    except Exception as e:
        logger.error(f"Error checking model access for user {user_id}, model {model_id}: {str(e)}")
        return False


def can_access_cohort_query(user_id: UUID, query_id: UUID, db: Session) -> bool:
    """
    Check if the user has access to the specified cohort query.

    Args:
        user_id (UUID): ID of the user
        query_id (UUID): ID of the cohort query
        db (Session): Database session

    Returns:
        bool: True if the user has access to the cohort query, False otherwise

    Raises:
        HTTPException: If there is an error during the access check
    """
    logger.debug(f"Checking if user: {user_id} can access cohort query: {query_id}")

    # First check if user has CAN_MANAGE_PROJECTS permission
    if has_permissions(user_id, [PermissionRef.CAN_MANAGE_PROJECTS], db):
        logger.debug(f"User: {user_id} has the {PermissionRef.CAN_MANAGE_PROJECTS} permission and is granted access.")
        return True

    try:
        """
            SELECT COUNT(projects.id)::int
            FROM projects
            INNER JOIN queries
                ON queries.project_id = projects.id AND queries.id = :query_id
            LEFT JOIN project_user_access
                ON project_user_access.project_id = projects.id
            WHERE (projects.owner_id = :userId
                OR project_user_access.userid = :userId)`
        """
        query = (
            select(Projects.id)
            .join(Queries)
            .outerjoin(ProjectUserAccess)
            .where((Projects.owner_id == user_id) | (ProjectUserAccess.user_id == user_id))
            .where((Queries.id == query_id) & (Queries.project_id == Projects.id))
        )
        result = db.exec(query)
        count = result.first()

        if not count:
            logger.debug(f"User: {user_id} is neither the project owner or an approved user and is not granted access.")
            return False

        logger.debug(f"User: {user_id} is either the project owner or an approved user and is granted access.")
        return True

    except Exception as e:
        logger.error(f"Error checking cohort query access for user {user_id}, query {query_id}: {str(e)}")
        return False


# The TypeScript code's event.authorizationToken usually maps to the 'Authorization' header.
API_KEY_HEADER_NAME = get_settings().PRIVATE_API_KEY_HEADER
api_key_header_scheme = APIKeyHeader(name=API_KEY_HEADER_NAME, auto_error=False)

# Name of the environment variable storing the expected private API key.
# This is the equivalent of `getSecret("privateApiKey")` in the TypeScript.
# EXPECTED_API_KEY_ENV_VAR = "PRIVATE_API_KEY"


def check_authorization_token(api_key: str = Security(api_key_header_scheme)) -> str:
    """
    Checks the provided API key against the expected key stored in an environment variable.

    This function is used as a FastAPI dependency to protect routes.
    It mirrors the logic of the TypeScript privateKeyAuthorizer.

    Args:
        api_key: The API key extracted from the request header.
                 FastAPI's Security utility injects this. If auto_error=False on
                 APIKeyHeader and the header is missing, api_key will be None.

    Raises:
        HTTPException:
            - 500 if the server is not configured with PRIVATE_API_KEY.
            - 401 if the API key is missing or invalid.

    Returns:
        The validated API key if it is correct.
    """
    expected_api_key = get_settings().PRIVATE_API_KEY

    if not expected_api_key:
        logger.critical(
            "CRITICAL: The environment variable for API key authentication is not set. "
            "The application cannot authenticate private API requests."
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server configuration error: API key mechanism not set up.",
        )

    if not api_key:
        # This handles the case where the header is missing (APIKeyHeader auto_error=False)
        logger.warning("Authentication attempt failed: API key was missing from the request.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated: API key is missing.",
            headers={"WWW-Authenticate": "ApiKey"},  # Standard header for API key challenges
        )

    # Direct string comparison is used, similar to 'authorizationToken === secretKey'
    # For very high-security scenarios, a constant-time comparison might be considered,
    # but for typical API keys, this is standard.
    if api_key == expected_api_key:
        logger.debug("API key authentication successful.")
        return api_key  # Return the key, signifying successful authentication
    else:
        logger.warning(f"Authentication attempt failed: Invalid API key provided. Submitted key: '{api_key[:5]}...'")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API Key.",
            headers={"WWW-Authenticate": "ApiKey"},
        )
