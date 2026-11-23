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
from datetime import datetime, timezone
from typing import NoReturn
from uuid import UUID

from fastapi import Depends, HTTPException, Security, status
from fastapi.security.api_key import APIKeyHeader
from sqlalchemy import update
from sqlmodel import Session, col, select

from flip_api.auth import trust_key_cache
from flip_api.auth.auth_utils import has_permissions
from flip_api.config import get_settings
from flip_api.db.database import get_session
from flip_api.db.models.main_models import Model, Projects, ProjectTrustIntersect, ProjectUserAccess, Queries, Trust
from flip_api.db.models.user_models import PermissionRef, RoleRef, UserRole
from flip_api.domain.schemas.actions import TrustAuditAction
from flip_api.trusts_services.utils.audit_helper import audit_trust_action
from flip_api.utils.logger import logger


def is_trust_admin_where_staged(user_id: UUID, project_id: UUID, db: Session) -> bool:
    """Whether the user is a Trust Admin of a trust the project is staged at (FLIP#1258).

    Deciding on a project needs its details, so a Trust Admin may READ projects staged at their trust — the
    staging rows are removed on unstaging, so an unstaged project is never covered. Grants no write access: the
    modify/contribute checks do not consult it.

    Args:
        user_id (UUID): ID of the user.
        project_id (UUID): ID of the project.
        db (Session): Database session.

    Returns:
        bool: True if the project is staged at a trust the user administers.
    """
    row = db.exec(
        select(ProjectTrustIntersect.id)
        .join(UserRole, col(UserRole.trust_id) == col(ProjectTrustIntersect.trust_id))
        .where(ProjectTrustIntersect.project_id == project_id)
        .where(UserRole.user_id == user_id)
        .where(UserRole.role_id == RoleRef.TRUST_ADMIN.value)
        .limit(1)
    ).first()
    return row is not None


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
        query = (
            select(Projects.id)
            # The outerjoin is load-bearing, not cosmetic. Without it SQLAlchemy puts
            # project_user_access in the FROM clause unjoined — `FROM projects, project_user_access`
            # — and the cartesian product means any access row belonging to the caller satisfies
            # the OR for any project. Same shape as can_access_model and can_access_cohort_query
            # below; the ON clause is inferred from ProjectUserAccess.project_id's foreign key.
            .outerjoin(ProjectUserAccess)
            .where((Projects.owner_id == user_id) | (ProjectUserAccess.user_id == user_id))
            .where(Projects.id == project_id)
            # The owner predicate is true for every joined access row, so without this the driver
            # buffers one row per project member where only the first is ever read.
            .limit(1)
        )
        result = db.exec(query)
        count = result.first()

        if not count:
            if is_trust_admin_where_staged(user_id, project_id, db):
                logger.debug(f"User: {user_id} administers a trust project {project_id} is staged at; read access.")
                return True
            logger.debug(f"User: {user_id} is neither the project owner or an approved user and is not granted access.")
            return False

        logger.debug(f"User: {user_id} is either the project owner or an approved user and is granted access.")
        return True

    except Exception as e:
        logger.error(f"Error checking project access for user {user_id}, project {project_id}: {str(e)}")
        return False


def can_modify_project(user_id: UUID, project_id: UUID, db: Session) -> bool:
    """
    Check if a user can perform project-level write operations (edit / stage / delete the project itself).

    Returns True for Admins (CAN_MANAGE_PROJECTS) and the project owner. Project membership alone
    does NOT unlock project-level writes — see :func:`can_contribute_to_project` for the looser
    check used by model-write endpoints.

    Ownership is deliberately NOT re-checked against the owner's current role: a user who created a
    project keeps project-level writes (edit / stage / delete, and cohort-submit via
    ``submit_cohort_query``) even if later demoted to Viewer. Ownership is the authority here, not the
    active role. This is an accepted gap — to fully revoke an owner's access, transfer ownership or
    delete the project. See the Viewer role notes in ``docs/source/sys-admin/admin-user-roles.rst``.

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


def can_contribute_to_project(user_id: UUID, project_id: UUID, db: Session) -> bool:
    """
    Check if a user can contribute artefacts (e.g. models) to a project.

    Looser than :func:`can_modify_project`: a Researcher who has been added to a project via
    ``ProjectUserAccess`` can contribute their own models even though they cannot edit the
    project itself. Viewers — who hold no permissions — are excluded by the
    ``CAN_CREATE_PROJECTS`` clause, so membership alone does not unlock writes for them.

    Returns True when any of the following holds:
        - The caller has ``CAN_MANAGE_PROJECTS`` (Admin), or
        - The caller is the project owner, or
        - The caller has a ``ProjectUserAccess`` row for the project AND holds
          ``CAN_CREATE_PROJECTS`` (Researcher member).

    Args:
        user_id (UUID): ID of the user
        project_id (UUID): ID of the project
        db (Session): Database session

    Returns:
        bool: True if the user can contribute to the project, False otherwise.
    """
    logger.debug(f"Checking if user: {user_id} can contribute to project: {project_id}")

    if has_permissions(user_id, [PermissionRef.CAN_MANAGE_PROJECTS], db):
        return True

    try:
        project = db.exec(select(Projects).where(Projects.id == project_id)).first()
        if not project:
            return False
        if project.owner_id == user_id:
            return True

        if not has_permissions(user_id, [PermissionRef.CAN_CREATE_PROJECTS], db):
            return False

        membership = db.exec(
            select(ProjectUserAccess.id)
            .where(ProjectUserAccess.project_id == project_id)
            .where(ProjectUserAccess.user_id == user_id)
        ).first()
        return membership is not None
    except Exception as e:
        logger.error(f"Error checking project contribute access for user {user_id}, project {project_id}: {str(e)}")
        return False


def can_modify_model(user_id: UUID, model_id: UUID, db: Session) -> bool:
    """
    Check if a user can perform write operations on a model.

    Allows:
        - Admins (``CAN_MANAGE_PROJECTS``).
        - The project owner (unrestricted across all models on the project).
        - The model's own ``owner_id``, but only if they could still contribute to the project
          (per :func:`can_contribute_to_project`). The contribution check is defence-in-depth:
          it locks a Viewer out even if they somehow ended up as ``Model.owner_id``.

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

        if can_modify_project(user_id, model.project_id, db):
            return True

        if model.owner_id != user_id:
            return False

        return can_contribute_to_project(user_id, model.project_id, db)
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
            project_id = db.exec(select(Queries.project_id).where(Queries.id == query_id)).first()
            if project_id is not None and is_trust_admin_where_staged(user_id, project_id, db):
                logger.debug(f"User: {user_id} administers a trust query {query_id}'s project is staged at; read.")
                return True
            logger.debug(f"User: {user_id} is neither the project owner or an approved user and is not granted access.")
            return False

        logger.debug(f"User: {user_id} is either the project owner or an approved user and is granted access.")
        return True

    except Exception as e:
        logger.error(f"Error checking cohort query access for user {user_id}, query {query_id}: {str(e)}")
        return False


API_KEY_HEADER_NAME = get_settings().TRUST_API_KEY_HEADER
api_key_header_scheme = APIKeyHeader(name=API_KEY_HEADER_NAME, auto_error=False)


# Compared against when a trust has no pending key, so every candidate costs two constant-time
# compares whether or not a rotation is in flight (a SHA-256 hex digest is 64 chars).
_NO_HASH = "0" * 64


def _as_utc(value: datetime) -> datetime:
    """A stored timestamp as UTC. The ``trust`` columns are ``timestamp without time zone``."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _pending_is_live(trust: Trust, now: datetime) -> bool:
    """Whether the trust has a staged key that has not passed its deadline (FLIP#1714).

    A staged key with no deadline counts as expired: every staged key is written with one, so a
    row without it is not something to accept for auth.
    """
    if trust.pending_api_key_hash is None:
        return False
    expires_at = trust.pending_api_key_expires_at
    return expires_at is not None and _as_utc(expires_at) > now


def _match(trust: Trust, provided_hash: str, now: datetime) -> tuple[bool, bool]:
    """``(matches the live key, matches the staged key)`` for one trust row, in constant time.

    Both compares always run, over equal-length digests, so the time taken does not depend on
    whether the row has a pending key or which of the two matched.
    """
    live = hmac.compare_digest(provided_hash, trust.api_key_hash or _NO_HASH) and trust.api_key_hash is not None
    pending_ok = _pending_is_live(trust, now)
    pending = hmac.compare_digest(provided_hash, trust.pending_api_key_hash or _NO_HASH) and pending_ok
    return live, pending


def _promote_pending_key(trust: Trust, provided_hash: str, db: Session, now: datetime) -> bool:
    """Make the staged key the trust's only key: the trust has its new kit (FLIP#1714).

    The swap is one conditional ``UPDATE`` that only applies while ``provided_hash`` is still the
    row's unexpired staged key, so it is decided in the database rather than from the row read
    earlier. Of two concurrent first calls with the new key one wins and writes the KEY_PROMOTED
    row; the other changes nothing. A ``--revoke-old`` or a UI rotate that commits first clears the
    staged hash, so the update matches no row and cannot bring the staged key back.

    The old key is refused from this commit on. Every worker's cache re-checks the live hash on a
    hit, so an old key cached before the promotion fails there and falls through to a sweep that no
    longer matches it.

    Returns:
        bool: Whether ``provided_hash`` is the trust's live key afterwards, whoever promoted it.
        False means the staged key lost to a cutover and the request must be refused.
    """
    result = db.execute(
        update(Trust)
        .where(
            col(Trust.id) == trust.id,
            col(Trust.pending_api_key_hash) == provided_hash,
            # The column is ``timestamp without time zone`` holding UTC.
            col(Trust.pending_api_key_expires_at) > now.astimezone(timezone.utc).replace(tzinfo=None),
        )
        .values(api_key_hash=provided_hash, pending_api_key_hash=None, pending_api_key_expires_at=None)
    )
    if result.rowcount == 1:  # type: ignore[attr-defined]
        audit_trust_action(
            trust_id=trust.id,
            trust_name=trust.name,
            action=TrustAuditAction.KEY_PROMOTED,
            user_id=None,
            session=db,
        )
        logger.info("Trust %r authenticated with its rotated key; the old key is now refused.", trust.name)
    db.commit()
    db.refresh(trust)
    trust_key_cache.invalidate()
    return trust.api_key_hash is not None and hmac.compare_digest(provided_hash, trust.api_key_hash)


def _refuse_superseded_key(trust: Trust) -> NoReturn:
    """401 for a staged key that a cutover replaced between the sweep and its promotion."""
    logger.warning("Trust %r presented a staged key that a later key change replaced; refused.", trust.name)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API Key.",
        headers={"WWW-Authenticate": "ApiKey"},
    )


def authenticate_trust(
    api_key: str = Security(api_key_header_scheme),
    db: Session = Depends(get_session),
) -> Trust:
    """Authenticate a trust by its per-trust API key and return the resolved row.

    The ``trust`` DB table is the sole registry: each row carries an ``api_key_hash``
    set when the trust is registered (admin UI or deploy-time CLI). This dependency
    hashes the provided key and walks every trust row whose ``api_key_hash`` is set,
    returning the matching ``Trust`` row. Identity (the row's ``id`` and ``name``)
    is then available to handlers without re-querying.

    During a staged rotation (FLIP#1714) a row also carries ``pending_api_key_hash``, and
    either key authenticates it. The first request made with the pending key promotes it:
    from then on only the new key works. A pending key past its deadline no longer
    authenticates, and the old key carries on; the scheduler clears it from the row
    (``expire_staged_trust_keys``), so this path writes nothing except a promotion.

    Constant-time comparison (``hmac.compare_digest``) on every candidate prevents
    timing side-channels (would otherwise leak which trust the key belongs to via
    early-exit timing).

    Args:
        api_key (str): The API key extracted from the request header.
        db (Session): DB session for the per-request hash lookup.

    Returns:
        Trust: The authenticated trust row.

    Raises:
        HTTPException: 401 if the key is missing or does not match any trust.
    """
    if not api_key:
        logger.warning("Trust authentication failed: API key missing from request.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated: API key is missing.",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    # SHA-256 is appropriate here: API keys are 256-bit random tokens from
    # `secrets.token_urlsafe(32)` (see generate_trust_key.py), not user-chosen
    # passwords. Slow KDFs (bcrypt/argon2) only defend against low-entropy inputs.
    # CodeQL flags this as `py/weak-sensitive-data-hashing` but the rule targets
    # password hashing — false positive for API-key storage.
    provided_hash = hashlib.sha256(api_key.encode()).hexdigest()
    now = datetime.now(timezone.utc)

    # Hot path: cache lookup, then a single PK fetch and constant-time compare.
    # On stale (deleted / hash rotated / key promoted) the live row check fails and
    # we fall through to the sweep — the cache can never grant access the DB denies.
    # See trust_key_cache.py.
    cached_trust_id = trust_key_cache.lookup(provided_hash)
    if cached_trust_id is not None:
        cached = db.get(Trust, cached_trust_id)
        if cached is not None:
            live, pending = _match(cached, provided_hash, now)
            if pending:
                if _promote_pending_key(cached, provided_hash, db, now):
                    return cached
                _refuse_superseded_key(cached)
            if live:
                logger.debug("Trust authenticated via cache.")
                return cached
        # Stale entry — fall through to the full sweep below. Don't 401 here;
        # the row may have been replaced (key rotated, trust re-registered).

    # Only rows with an api_key_hash are auth candidates — a row can briefly
    # exist mid-insert before register_trust stamps the hash.
    candidates = db.exec(
        select(Trust).where(
            Trust.api_key_hash.is_not(None),  # type: ignore[union-attr]
        )
    ).all()

    matched: Trust | None = None
    matched_pending = False
    # SECURITY INVARIANTS — do not "optimise" any of these away:
    #   1. NO `break` on match. The full sweep is the timing property: per-key
    #      response time must not depend on which trust matched (or whether any
    #      trust matched). A `break` would leak match-position via timing.
    #   2. `hmac.compare_digest` is constant-time ONLY over equal-length hex
    #      strings. Every `api_key_hash` and `pending_api_key_hash` is a SHA-256
    #      hex digest (64 chars), as is `_NO_HASH` — if the column type or hash
    #      algorithm ever changes, re-check this.
    #   3. The trust_key_cache hot path above must preserve both invariants
    #      (constant-time compares over equal-length digests; no early-exit
    #      shortcuts that depend on the cached match).
    for candidate in candidates:
        live, pending = _match(candidate, provided_hash, now)
        if live or pending:
            matched, matched_pending = candidate, pending

    if matched is not None:
        if matched_pending and not _promote_pending_key(matched, provided_hash, db, now):
            _refuse_superseded_key(matched)
        # Cache the resolved id so the next request short-circuits to the hot
        # path. Done only after a sweep match — never after the cache-stale
        # fall-through, where the candidate set may have shifted under us.
        trust_key_cache.remember(provided_hash, matched.id)
        logger.debug("Trust authenticated successfully (cache miss).")
        return matched

    logger.warning("Trust authentication failed: no matching trust for provided key")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API Key.",
        headers={"WWW-Authenticate": "ApiKey"},
    )
