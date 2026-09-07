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

"""
Task handlers for processing tasks received from the central hub via polling.

Each handler processes a specific task type and returns a result dict.
"""

import datetime
import hashlib
import json
from typing import Any

from fastapi import HTTPException

from trust_api.config import get_settings
from trust_api.routers.schemas import (
    CentralHubProject,
    CohortQueryInput,
    DeleteImagingInput,
    GetImagingStatusInput,
    PersistCohortInput,
    ReimportStudiesInput,
    UpdateProfileRequest,
)
from trust_api.utils.http import make_request
from trust_api.utils.logger import logger

DATA_ACCESS_API_URL = get_settings().DATA_ACCESS_API_URL
CENTRAL_HUB_API_URL = get_settings().CENTRAL_HUB_API_URL
IMAGING_API_URL = get_settings().IMAGING_API_URL
TRUST_API_KEY = get_settings().TRUST_API_KEY
TRUST_API_KEY_HEADER = get_settings().TRUST_API_KEY_HEADER
TRUST_INTERNAL_SERVICE_KEY = get_settings().TRUST_INTERNAL_SERVICE_KEY
TRUST_INTERNAL_SERVICE_KEY_HEADER = get_settings().TRUST_INTERNAL_SERVICE_KEY_HEADER
AES_KEY_BASE64 = get_settings().AES_KEY_BASE64
COHORT_ADMIN_KEY_HEADER = get_settings().COHORT_ADMIN_KEY_HEADER


def trust_internal_headers() -> dict[str, str]:
    """Return the auth header sent on every trust-internal call.

    Used for imaging-api and data-access-api requests; both validate the same
    per-trust ``TRUST_INTERNAL_SERVICE_KEY`` against their stored hash.

    Returns:
        dict[str, str]: Single-entry dict mapping the configured header name
        to the trust-internal service key.
    """
    return {TRUST_INTERNAL_SERVICE_KEY_HEADER: TRUST_INTERNAL_SERVICE_KEY}


def cohort_admin_headers() -> dict[str, str]:
    """Headers for data-access-api's cohort-DEFINING write routes (snapshot create/delete).

    Those routes require the trust-internal key AND proof of possessing ``AES_KEY_BASE64``
    (FLIP#857) — the second gate is what stops fl-client's researcher code from rewriting or
    deleting a project's frozen cohort, since fl-client holds no AES key. The proof is the
    SHA-256 of the key, never the key itself, so it stays off the wire and out of logs. Layers
    the cohort-admin header on top of the trust-internal one.

    Returns:
        dict[str, str]: The trust-internal header plus the cohort-admin proof header.
    """
    proof = hashlib.sha256(AES_KEY_BASE64.encode()).hexdigest()
    return {**trust_internal_headers(), COHORT_ADMIN_KEY_HEADER: proof}


# Task type constants — must match TaskType enum in flip-api/src/flip_api/domain/schemas/status.py
TASK_COHORT_QUERY = "cohort_query"
TASK_CREATE_IMAGING = "create_imaging"
TASK_DELETE_IMAGING = "delete_imaging"
TASK_GET_IMAGING_STATUS = "get_imaging_status"
TASK_REIMPORT_STUDIES = "reimport_studies"
TASK_UPDATE_USER_PROFILE = "update_user_profile"
TASK_PERSIST_COHORT = "persist_cohort"


async def handle_persist_cohort(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Freeze the approved cohort on this trust (FLIP#857).

    Forwards the approval-time snapshot request to the local data-access-api, which runs
    the approved query ONCE and persists the resulting dataframe as the project's frozen
    artefact — the only thing the row-level routes serve from then on. The snapshot
    response (aggregates only: row count, column names, timestamps) is returned as the
    task result verbatim, so the hub can record its frozen-cohort audit row from it.

    Failure (including a below-threshold cohort, which data-access-api refuses with 403)
    marks the task FAILED at the hub with the category-only detail — nothing is persisted
    trust-side in that case, and the project's row-level routes keep refusing.

    Args:
        payload: Task payload matching ``PersistCohortInput``.

    Returns:
        dict with success status, the snapshot facts as ``result``, or error details.
    """
    logger.info(f"Processing cohort snapshot task: project_id={payload.get('project_id')}")

    try:
        request = PersistCohortInput(**payload)
        response = await make_request(
            method="POST",
            url=f"{DATA_ACCESS_API_URL}/cohort/snapshot",
            json_body={
                "encrypted_project_id": request.encrypted_project_id,
                "query": request.query,
            },
            # Snapshot creation is a cohort-DEFINING write: it needs the cohort-admin proof on
            # top of the trust-internal key (FLIP#857).
            headers=cohort_admin_headers(),
            # Snapshot creation runs the full cohort query, so it inherits the cohort
            # query's timeout rather than the default request timeout.
            timeout_seconds=get_settings().COHORT_QUERY_TIMEOUT_SECONDS,
        )
        logger.info(
            f"Cohort snapshot persisted for project {request.project_id}: "
            f"{response.get('row_count')} rows"  # type: ignore[union-attr]
        )
        return {"success": True, "result": json.dumps(response)}
    except Exception as e:
        logger.error(f"Error persisting cohort snapshot: {e}")
        return {"success": False, "error": str(e)}


async def handle_cohort_query(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Process a cohort query task.

    Calls the local data-access-api, then pushes results back to the central hub.
    On data-access-api failure, posts an error report to the hub so the per-trust
    UI status switches from "running" to "error" instead of staying stuck.

    Args:
        payload: Task payload containing query details (query, query_name, encrypted_project_id, etc.)

    Returns:
        dict with success status and any error details.
    """
    logger.info(f"Processing cohort query task: query_id={payload.get('query_id')}")

    try:
        CohortQueryInput(**payload)
        # Post cohort query to the local data access API
        response = await make_request(
            method="POST",
            url=f"{DATA_ACCESS_API_URL}/cohort",
            json_body=payload,
            headers=trust_internal_headers(),
            timeout_seconds=get_settings().COHORT_QUERY_TIMEOUT_SECONDS,
        )

        # Convert all 'value' fields to strings before sending
        for group in response.get("data", []):  # type: ignore[union-attr]
            for result in group.get("results", []):  # type: ignore[union-attr, attr-defined]
                if "value" in result:
                    result["value"] = str(result["value"])

        # Send results back to the central hub
        await make_request(
            method="POST",
            url=f"{CENTRAL_HUB_API_URL}/cohort/results",
            json_body=response,
            headers={TRUST_API_KEY_HEADER: TRUST_API_KEY},
        )

        logger.info(f"Cohort query completed: query_id={payload.get('query_id')}")
        return {"success": True}

    except Exception as e:
        logger.error(f"Error processing cohort query: {e}")
        await _report_cohort_error_to_hub(payload, str(e))
        return {"success": False, "error": str(e)}


async def _report_cohort_error_to_hub(payload: dict[str, Any], error: str) -> None:
    """Tell the hub this trust's cohort query failed.

    Without this, the hub never sees the trust at all for this query and the UI
    leaves the per-trust chip on "running" forever. We swallow any failure here
    because the original cohort error is what callers care about — surfacing a
    hub-post failure would mask the real cause in the task result.
    """
    query_id = payload.get("query_id")
    trust_id = payload.get("trust_id")
    if not query_id or not trust_id:
        # Payload was malformed before we could extract identifiers; nothing to
        # post — the upstream error already explains what went wrong.
        return
    try:
        await make_request(
            method="POST",
            url=f"{CENTRAL_HUB_API_URL}/cohort/results",
            json_body={
                "query_id": query_id,
                "trust_id": trust_id,
                "created": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d"),
                "record_count": 0,
                "data": [],
                "error": error,
            },
            headers={TRUST_API_KEY_HEADER: TRUST_API_KEY},
        )
    except Exception as hub_exc:
        logger.warning(
            f"Failed to report cohort error to hub for query_id={query_id}, trust_id={trust_id}: {hub_exc}"
        )


async def handle_create_imaging(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Process an imaging project creation task.

    Calls the local imaging-api to create the project.

    Args:
        payload: Task payload containing project details.

    Returns:
        dict with success status and imaging project result.
    """
    logger.info(f"Processing create imaging task: project_id={payload.get('project_id')}")

    try:
        CentralHubProject(**payload)
        response = await make_request(
            method="POST",
            url=f"{IMAGING_API_URL}/projects/create-project-from-central-hub-project",
            json_body=payload,
            headers=trust_internal_headers(),
        )

        logger.info(f"Imaging project created: id={response.get('ID')}, name={response.get('name')}")
        return {"success": True, "result": json.dumps(response)}

    except Exception as e:
        logger.error(f"Error creating imaging project: {e}")
        return {"success": False, "error": str(e)}


async def handle_delete_imaging(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Process an imaging project deletion task.

    Args:
        payload: Task payload containing imaging_project_id.

    Returns:
        dict with success status.
    """
    logger.info(f"Processing delete imaging task: {payload.get('imaging_project_id')}")

    try:
        validated = DeleteImagingInput(**payload)
        imaging_project_id = validated.imaging_project_id
        # imaging-api exposes DELETE /projects/{project_id} as a PATH parameter. Passing it as a
        # query parameter against /projects/ matched no route and returned 405, so the deletion
        # silently never happened while the hub recorded it as done. See FLIP#963.
        await make_request(
            method="DELETE",
            url=f"{IMAGING_API_URL}/projects/{imaging_project_id}",
            headers=trust_internal_headers(),
        )

        logger.info(f"Imaging project deleted: {imaging_project_id}")
        return {"success": True}

    except Exception as e:
        logger.error(f"Error deleting imaging project: {e}")
        return {"success": False, "error": str(e)}


async def handle_get_imaging_status(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Process an imaging status retrieval task.

    Gets the import status from the local imaging-api and reports back to the hub.

    On failure the upstream HTTP status code is reported alongside the error, because the hub
    renders a missing XNAT project (imaging-api 404) differently from an unreachable one
    (500/502) — see FLIP#1022. Without it the hub would have to recover the code by parsing the
    stringified exception.

    Args:
        payload: Task payload containing imaging_project_id and encoded_query.

    Returns:
        dict with success status and imaging status result. On failure, ``status_code`` is
        present only when the failure carried one.
    """
    logger.info(f"Processing get imaging status task: {payload.get('imaging_project_id')}")

    try:
        validated = GetImagingStatusInput(**payload)
        imaging_project_id = validated.imaging_project_id
        encoded_query = validated.encoded_query
        response = await make_request(
            method="GET",
            url=f"{IMAGING_API_URL}/retrieval/import_status_count/{imaging_project_id}",
            params={"encoded_query": encoded_query},
            headers=trust_internal_headers(),
        )

        logger.info(f"Imaging status retrieved: {imaging_project_id}")
        return {"success": True, "result": json.dumps(response)}

    except HTTPException as e:
        logger.error(f"Error getting imaging status: {e}")
        return {"success": False, "error": str(e), "status_code": e.status_code}
    except Exception as e:
        logger.error(f"Error getting imaging status: {e}")
        return {"success": False, "error": str(e)}


async def handle_reimport_studies(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Process a reimport studies task.

    Args:
        payload: Task payload containing imaging_project_id and encoded_query.

    Returns:
        dict with success status.
    """
    logger.info(f"Processing reimport studies task: {payload.get('imaging_project_id')}")

    try:
        validated = ReimportStudiesInput(**payload)
        imaging_project_id = validated.imaging_project_id
        encoded_query = validated.encoded_query
        await make_request(
            method="PUT",
            url=f"{IMAGING_API_URL}/retrieval/reimport_imaging_project_studies/{imaging_project_id}",
            params={"encoded_query": encoded_query},
            headers=trust_internal_headers(),
        )

        logger.info(f"Reimport initiated: {imaging_project_id}")
        return {"success": True}

    except Exception as e:
        logger.error(f"Error reimporting studies: {e}")
        return {"success": False, "error": str(e)}


async def handle_update_user_profile(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Process a user profile update task.

    Args:
        payload: Task payload containing email and enabled fields.

    Returns:
        dict with success status.
    """
    logger.info(f"Processing update user profile task: email={payload.get('email')}")

    try:
        UpdateProfileRequest(**payload)
        await make_request(
            method="PUT",
            url=f"{IMAGING_API_URL}/users",
            json_body=payload,
            headers=trust_internal_headers(),
        )

        logger.info(f"User profile updated: {payload.get('email')}")
        return {"success": True}

    except Exception as e:
        logger.error(f"Error updating user profile: {e}")
        return {"success": False, "error": str(e)}


# Registry mapping task types to their handlers
TASK_HANDLERS = {
    TASK_COHORT_QUERY: handle_cohort_query,
    TASK_CREATE_IMAGING: handle_create_imaging,
    TASK_DELETE_IMAGING: handle_delete_imaging,
    TASK_GET_IMAGING_STATUS: handle_get_imaging_status,
    TASK_REIMPORT_STUDIES: handle_reimport_studies,
    TASK_UPDATE_USER_PROFILE: handle_update_user_profile,
    TASK_PERSIST_COHORT: handle_persist_cohort,
}
