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

import hashlib
import hmac
import json
from uuid import UUID

from fastapi import HTTPException, Security, status
from fastapi.security.api_key import APIKeyHeader
from sqlmodel import Session, select

from flip_api.auth.auth_utils import has_permissions
from flip_api.config import get_settings
from flip_api.db.models.main_models import Model, Projects, ProjectUserAccess, Queries
from flip_api.db.models.user_models import PermissionRef
from flip_api.utils.get_secrets import get_secret
from flip_api.utils.logger import logger


def can_access_project(user_id: UUID, project_id: UUID, db: Session) -> bool:
    """
    Check if a user has access to a specific project.

    Args:
        user_id (UUID): ID of the user
        project_id (UUID): ID of the project
        db (Session): Database session

    Returns:
        bool: True if the user has access to the project, False otherwise
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


def can_modify_project(user_id: UUID, project_id: UUID, db: Session) -> bool:
    """
    Check if a user can perform write operations on a project.

    Returns True for users with CAN_MANAGE_PROJECTS permission (Admins, Researchers)
    or for the project owner. Returns False for Observers.

    Args:
        user_id (UUID): ID of the user
        project_id (UUID): ID of the project
        db (Session): Database session

    Returns:
        bool: True if the user can modify the project, False otherwise
    """
    logger.debug(f"Checking if user: {user_id} can modify project: {project_id}")

    if has_permissions(user_id, [PermissionRef.CAN_MANAGE_PROJECTS], db):
        return True

    try:
        project = db.exec(select(Projects).where(Projects.id == project_id)).first()
        if project and project.owner_id == user_id:
            return True
    except Exception as e:
        logger.error(f"Error checking project modify access for user {user_id}, project {project_id}: {str(e)}")

    return False


def can_modify_model(user_id: UUID, model_id: UUID, db: Session) -> bool:
    """
    Check if a user can perform write operations on a model.

    Looks up the model's project_id, then delegates to can_modify_project.

    Args:
        user_id (UUID): ID of the user
        model_id (UUID): ID of the model
        db (Session): Database session

    Returns:
        bool: True if the user can modify the model, False otherwise
    """
    logger.debug(f"Checking if user: {user_id} can modify model: {model_id}")

    if has_permissions(user_id, [PermissionRef.CAN_MANAGE_PROJECTS], db):
        return True

    try:
        model = db.exec(select(Model).where(Model.id == model_id)).first()
        if not model or not model.project_id:
            return False
        return can_modify_project(user_id, model.project_id, db)
    except Exception as e:
        logger.error(f"Error checking model modify access for user {user_id}, model {model_id}: {str(e)}")
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


API_KEY_HEADER_NAME = get_settings().TRUST_API_KEY_HEADER
api_key_header_scheme = APIKeyHeader(name=API_KEY_HEADER_NAME, auto_error=False)


def verify_trust_identity(trust_name: str, authenticated_trust: str) -> None:
    """Verify the authenticated trust matches the expected trust name.

    Args:
        trust_name (str): The trust name from the URL path.
        authenticated_trust (str): The trust name from API key authentication.

    Raises:
        HTTPException: 403 if the names do not match.
    """
    if authenticated_trust != trust_name:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Trust '{authenticated_trust}' is not authorised to act as '{trust_name}'",
        )


_trust_api_key_hashes_cache: dict[str, str] | None = None


def _get_trust_api_key_hashes() -> dict[str, str]:
    """Get trust API key hashes from env var (dev) or AWS Secrets Manager (prod).

    Cached after first call — the hashes do not change during the lifetime of a process.

    Returns:
        dict[str, str]: Mapping of trust names to SHA-256 hex digests of their API keys.
    """
    global _trust_api_key_hashes_cache  # noqa: PLW0603
    if _trust_api_key_hashes_cache is not None:
        return _trust_api_key_hashes_cache

    stt = get_settings()
    if stt.ENV == "production":
        _trust_api_key_hashes_cache = json.loads(get_secret("trust_api_key_hashes"))
    else:
        _trust_api_key_hashes_cache = stt.TRUST_API_KEY_HASHES

    return _trust_api_key_hashes_cache


INTERNAL_SERVICE_KEY_HEADER_NAME = get_settings().INTERNAL_SERVICE_KEY_HEADER
internal_key_header_scheme = APIKeyHeader(name=INTERNAL_SERVICE_KEY_HEADER_NAME, auto_error=False)

_internal_service_key_hash_cache: str | None = None


def _get_internal_service_key_hash() -> str:
    """Get internal service key hash from env var (dev) or AWS Secrets Manager (prod).

    Cached after first call — the hash does not change during the lifetime of a process.

    Returns:
        str: SHA-256 hex digest of the internal service key, or empty string if not configured.
    """
    global _internal_service_key_hash_cache  # noqa: PLW0603
    if _internal_service_key_hash_cache is not None:
        return _internal_service_key_hash_cache

    stt = get_settings()
    if stt.ENV == "production":
        _internal_service_key_hash_cache = get_secret("internal_service_key_hash")
    else:
        _internal_service_key_hash_cache = stt.INTERNAL_SERVICE_KEY_HASH

    return _internal_service_key_hash_cache


def authenticate_internal_service(api_key: str = Security(internal_key_header_scheme)) -> None:
    """Authenticate an internal service (e.g., fl-server on the Central Hub).

    The fl-server sends an internal service key in the X-Internal-Service-Key header.
    This dependency hashes the provided key and compares it against the stored hash
    using constant-time comparison.

    Args:
        api_key (str): The internal service key from the request header.

    Raises:
        HTTPException: 401 if the key is missing, unconfigured, or invalid.
    """
    if not api_key:
        logger.warning("Internal service authentication failed: key missing from request.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated: internal service key is missing.",
        )
    expected_hash = _get_internal_service_key_hash()
    if not expected_hash:
        logger.warning("Internal service authentication failed: INTERNAL_SERVICE_KEY_HASH not configured.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Internal service auth not configured.",
        )
    provided_hash = hashlib.sha256(api_key.encode()).hexdigest()
    if not hmac.compare_digest(provided_hash, expected_hash):
        logger.warning("Internal service authentication failed: invalid key.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal service key.",
        )


def authenticate_trust(api_key: str = Security(api_key_header_scheme)) -> str:
    """Authenticate a trust by its per-trust API key and return the trust name.

    Each trust has a unique API key. The hub stores SHA-256 hashes of these keys
    in TRUST_API_KEY_HASHES (env var in dev, AWS Secrets Manager in prod).
    This dependency hashes the provided key, looks it up in the mapping, and
    returns the authenticated trust name.

    Uses hmac.compare_digest for constant-time comparison to prevent timing attacks.

    Args:
        api_key (str): The API key extracted from the request header.

    Raises:
        HTTPException: 401 if the key is missing or does not match any trust.

    Returns:
        str: The name of the authenticated trust (e.g. "Trust_1").
    """
    if not api_key:
        logger.warning("Trust authentication failed: API key missing from request.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated: API key is missing.",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    provided_hash = hashlib.sha256(api_key.encode()).hexdigest()
    trust_hashes = _get_trust_api_key_hashes()

    # Iterate all entries with constant-time comparison to prevent timing side-channels.
    for trust_name, stored_hash in trust_hashes.items():
        if hmac.compare_digest(provided_hash, stored_hash):
            logger.debug("Trust authenticated successfully.")
            return trust_name

    logger.warning("Trust authentication failed: no matching trust for provided key")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API Key.",
        headers={"WWW-Authenticate": "ApiKey"},
    )
