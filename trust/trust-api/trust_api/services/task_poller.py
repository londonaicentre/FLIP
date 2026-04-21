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
Background polling service that periodically fetches tasks from the central hub
and dispatches them to local handlers.

This replaces the previous model where the hub made inbound HTTP requests to the trust.
Now all communication is outbound from the trust to the hub.
"""

import asyncio
import json

import httpx

from trust_api.config import get_settings
from trust_api.services.task_handlers import TASK_HANDLERS
from trust_api.utils.encryption import decrypt
from trust_api.utils.logger import logger

CENTRAL_HUB_API_URL = get_settings().CENTRAL_HUB_API_URL
TRUST_API_KEY = get_settings().TRUST_API_KEY
TRUST_API_KEY_HEADER = get_settings().TRUST_API_KEY_HEADER
TRUST_NAME = get_settings().TRUST_NAME
POLL_INTERVAL_SECONDS = get_settings().POLL_INTERVAL_SECONDS


def _auth_headers() -> dict[str, str]:
    """Return authentication headers for hub API calls.

    Returns:
        dict[str, str]: Single-entry mapping of the trust API key header to the configured key.
    """
    return {TRUST_API_KEY_HEADER: TRUST_API_KEY}


async def _poll_for_tasks(client: httpx.AsyncClient) -> list[dict]:
    """
    Poll the central hub for pending tasks.

    Args:
        client: HTTP client for making requests.

    Returns:
        List of pending task dicts from the hub.
    """
    try:
        response = await client.get(
            f"{CENTRAL_HUB_API_URL}/tasks/{TRUST_NAME}/pending",
            headers=_auth_headers(),
        )
        if response.status_code != 200:
            logger.warning(f"Unexpected status {response.status_code} polling for tasks")
            return []

        tasks = response.json()
        if tasks:
            logger.info(f"Received {len(tasks)} pending tasks from hub")
        return tasks
    except Exception as e:
        logger.error(f"Error polling for tasks: {e}")
        return []


async def _send_heartbeat(client: httpx.AsyncClient) -> None:
    """
    Send a heartbeat to the central hub to indicate this trust is online.

    Args:
        client: HTTP client for making requests.
    """
    try:
        await client.post(
            f"{CENTRAL_HUB_API_URL}/trust/{TRUST_NAME}/heartbeat",
            headers=_auth_headers(),
        )
        logger.debug("Heartbeat sent successfully")
    except Exception as e:
        logger.error(f"Error sending heartbeat: {e}")


_REPORT_MAX_RETRIES = 3
_REPORT_RETRY_DELAY_SECONDS = 2


async def _report_task_result(client: httpx.AsyncClient, task_id: str, result: dict) -> None:
    """
    Report the result of a completed task back to the central hub.

    Retries up to ``_REPORT_MAX_RETRIES`` times with exponential backoff on failure,
    since a lost result can leave a task permanently stuck in IN_PROGRESS on the hub.

    Args:
        client: HTTP client for making requests.
        task_id: The ID of the completed task.
        result: The result dict containing success status and optional result data.
    """
    result_data = result.get("result")
    # Include error details in the result field when reporting failures
    if not result.get("success", False) and result.get("error") and not result_data:
        result_data = json.dumps({"error": result["error"]})

    payload = {
        "success": result.get("success", False),
        "result": result_data,
    }

    for attempt in range(_REPORT_MAX_RETRIES):
        try:
            response = await client.post(
                f"{CENTRAL_HUB_API_URL}/tasks/{TRUST_NAME}/{task_id}/result",
                headers=_auth_headers(),
                json=payload,
            )
            if response.status_code == 200:
                logger.debug(f"Reported result for task {task_id}")
                return
            logger.warning(
                f"Unexpected status {response.status_code} reporting result for task {task_id} "
                f"(attempt {attempt + 1}/{_REPORT_MAX_RETRIES})"
            )
        except Exception as e:
            logger.warning(
                f"Error reporting result for task {task_id} "
                f"(attempt {attempt + 1}/{_REPORT_MAX_RETRIES}): {e}"
            )
        if attempt < _REPORT_MAX_RETRIES - 1:
            delay = _REPORT_RETRY_DELAY_SECONDS * (2**attempt)
            await asyncio.sleep(delay)

    logger.error(f"Failed to report result for task {task_id} after {_REPORT_MAX_RETRIES} attempts")


async def _process_task(task: dict) -> dict:
    """
    Process a single task by dispatching to the appropriate handler.

    Args:
        task: Task dict with id, task_type, and payload fields.

    Returns:
        Result dict with success status.
    """
    task_type = task.get("task_type", "")
    task_id = task.get("id", "unknown")
    payload_str = task.get("payload", "{}")

    handler = TASK_HANDLERS.get(task_type)
    if not handler:
        logger.error(f"Unknown task type: {task_type} for task {task_id}")
        return {"success": False, "error": f"Unknown task type: {task_type}"}

    try:
        payload = json.loads(decrypt(payload_str))
    except (ValueError, json.JSONDecodeError) as e:
        logger.error(f"Failed to decrypt or parse payload for task {task_id}: {e}")
        return {"success": False, "error": f"Invalid payload: {e}"}

    logger.info(f"Processing task {task_id} (type={task_type})")
    return await handler(payload)


async def run_poller() -> None:
    """
    Main polling loop. Runs indefinitely, polling the hub for tasks and processing them.

    This function is started as a background task during the FastAPI lifespan.
    """
    logger.info(
        f"Starting task poller for trust {TRUST_NAME}, "
        f"polling every {POLL_INTERVAL_SECONDS}s from {CENTRAL_HUB_API_URL}"
    )

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        while True:
            try:
                # Send heartbeat
                await _send_heartbeat(client)

                # Poll for tasks
                tasks = await _poll_for_tasks(client)

                # Process tasks sequentially (could be parallelized if needed)
                for task in tasks:
                    task_id = task.get("id", "unknown")
                    try:
                        result = await _process_task(task)
                        await _report_task_result(client, task_id, result)
                    except Exception as e:
                        logger.error(f"Unhandled error processing task {task_id}: {e}")
                        await _report_task_result(
                            client, task_id, {"success": False, "error": str(e)}
                        )

            except Exception as e:
                logger.error(f"Error in polling loop: {e}")

            await asyncio.sleep(POLL_INTERVAL_SECONDS)
