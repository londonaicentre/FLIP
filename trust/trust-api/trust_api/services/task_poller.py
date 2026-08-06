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

"""Background polling service: heartbeat → pull pending tasks → dispatch → report.

All communication is outbound from the trust to the hub. The hub identifies the
trust by API key alone — there is no ``{trust_name}`` segment in any URL.

On the first successful heartbeat the hub returns ``{trust_id, trust_name, …}``;
we log the resolved identity (so the operator can see which trust this host
came up as) and, when ``EXPECTED_TRUST_ID`` is set in the kit file, verify the
hub's answer against it. A mismatch means the wrong kit was deployed to the
wrong host — we exit the process so a process-supervisor restart loop surfaces
the misconfiguration loudly rather than the host silently acting as the wrong
trust.
"""

import asyncio
import json
import sys

import httpx

from trust_api.config import get_settings
from trust_api.services.health_collector import current_snapshot
from trust_api.services.task_handlers import TASK_HANDLERS
from trust_api.utils.encryption import decrypt
from trust_api.utils.logger import logger

CENTRAL_HUB_API_URL = get_settings().CENTRAL_HUB_API_URL
TRUST_API_KEY = get_settings().TRUST_API_KEY
TRUST_API_KEY_HEADER = get_settings().TRUST_API_KEY_HEADER
EXPECTED_TRUST_ID = get_settings().EXPECTED_TRUST_ID
POLL_INTERVAL_SECONDS = get_settings().POLL_INTERVAL_SECONDS

# Flips True on the first heartbeat that returns a valid identity. Subsequent
# heartbeats skip the log + EXPECTED_TRUST_ID comparison so we don't spam.
_identity_logged = False


def _auth_headers() -> dict[str, str]:
    """Return authentication headers for hub API calls.

    Returns:
        dict[str, str]: Single-entry mapping of the trust API key header to the configured key.
    """
    return {TRUST_API_KEY_HEADER: TRUST_API_KEY}


def _maybe_announce_identity(body: dict) -> None:
    """Log resolved identity from the hub once; abort on EXPECTED_TRUST_ID mismatch.

    Called from every successful trust-facing response. The hub embeds
    ``{trust_id, trust_name}`` in every response body so the trust can identify
    itself; we only act on the first one (idempotent thanks to ``_identity_logged``).

    Args:
        body (dict): Parsed JSON response from the hub.

    Raises:
        SystemExit: If ``EXPECTED_TRUST_ID`` is set and disagrees with ``trust_id``.
    """
    global _identity_logged  # noqa: PLW0603
    if _identity_logged:
        return
    trust_id = body.get("trust_id")
    trust_name = body.get("trust_name")
    if not trust_id or not trust_name:
        return  # not an identity-bearing response (e.g. an error body)

    if EXPECTED_TRUST_ID and trust_id != EXPECTED_TRUST_ID:
        # Loud, fatal: this host has been deployed with the wrong kit.
        logger.error(
            "Hub identifies this host as trust %r (%s); kit declares "
            "EXPECTED_TRUST_ID=%s. Wrong kit deployed to this host? Exiting.",
            trust_name,
            trust_id,
            EXPECTED_TRUST_ID,
        )
        sys.exit(2)

    logger.info("Authenticated to hub as trust %r (%s).", trust_name, trust_id)
    _identity_logged = True


async def _poll_for_tasks(client: httpx.AsyncClient) -> list[dict]:
    """Poll the central hub for pending tasks.

    Args:
        client (httpx.AsyncClient): HTTP client for making requests.

    Returns:
        list[dict]: Pending task dicts from the hub (encrypted payloads).
    """
    try:
        response = await client.get(
            f"{CENTRAL_HUB_API_URL}/tasks/pending",
            headers=_auth_headers(),
        )
        if response.status_code != 200:
            logger.warning(f"Unexpected status {response.status_code} polling for tasks")
            return []

        body = response.json()
        _maybe_announce_identity(body)
        tasks = body.get("tasks", [])
        if tasks:
            logger.info(f"Received {len(tasks)} pending tasks from hub")
        return tasks
    except Exception as e:
        logger.error(f"Error polling for tasks: {e}")
        return []


async def _send_heartbeat(client: httpx.AsyncClient) -> None:
    """Send a heartbeat to the central hub and run the identity self-check.

    Args:
        client (httpx.AsyncClient): HTTP client for making requests.
    """
    try:
        # Attach the latest service-health snapshot when the collector has one; a
        # bodyless POST is the pre-collector wire behavior and must keep working.
        kwargs: dict = {"headers": _auth_headers()}
        snapshot = current_snapshot()
        if snapshot is not None:
            kwargs["json"] = snapshot
        response = await client.post(f"{CENTRAL_HUB_API_URL}/trust/heartbeat", **kwargs)
        # httpx only raises on transport errors — auth/identity rejections
        # (401/403/404) come back as a successful HTTP response. Without this
        # check the trust silently never updates ``trust.last_heartbeat`` on
        # the hub and gets reported "offline" in Connection Status while
        # actually polling normally.
        if response.is_success:
            logger.debug("Heartbeat sent successfully")
            try:
                _maybe_announce_identity(response.json())
            except ValueError:
                pass  # body wasn't JSON; not fatal
        else:
            logger.error(
                f"Heartbeat rejected by hub: HTTP {response.status_code} — "
                f"{response.text[:200]}"
            )
    except Exception as e:
        logger.error(f"Error sending heartbeat: {e}")


_REPORT_MAX_RETRIES = 3
_REPORT_RETRY_DELAY_SECONDS = 2


async def _report_task_result(client: httpx.AsyncClient, task_id: str, result: dict) -> None:
    """Report the result of a completed task back to the central hub.

    Retries up to ``_REPORT_MAX_RETRIES`` times with exponential backoff on failure,
    since a lost result can leave a task permanently stuck in IN_PROGRESS on the hub.

    Args:
        client (httpx.AsyncClient): HTTP client for making requests.
        task_id (str): The ID of the completed task.
        result (dict): Result dict with ``success`` and optional ``result`` / ``error`` fields.
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
                f"{CENTRAL_HUB_API_URL}/tasks/{task_id}/result",
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
    """Process a single task by dispatching to the appropriate handler.

    Args:
        task (dict): Task dict with ``id``, ``task_type``, and ``payload`` fields.

    Returns:
        dict: Result dict with ``success`` status (and optional ``result`` / ``error``).
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
    """Main polling loop. Runs indefinitely, polling the hub for tasks and processing them.

    This function is started as a background task during the FastAPI lifespan.
    """
    logger.info(
        f"Starting task poller (key-only auth), polling every {POLL_INTERVAL_SECONDS}s "
        f"from {CENTRAL_HUB_API_URL}. Identity is reported by the hub on first contact."
    )

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        while True:
            try:
                # Send heartbeat (first call also runs the EXPECTED_TRUST_ID self-check).
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
