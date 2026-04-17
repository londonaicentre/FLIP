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

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trust_api.services.task_poller import (
    _poll_for_tasks,
    _process_task,
    _report_task_result,
    _send_heartbeat,
    run_poller,
)

# ---- _poll_for_tasks ----


@pytest.mark.asyncio
async def test_poll_for_tasks_success():
    """Should return tasks from the hub on 200 response."""
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [
        {"id": "task-1", "task_type": "cohort_query", "payload": '{"query": "SELECT 1"}'},
    ]
    mock_client.get.return_value = mock_response

    tasks = await _poll_for_tasks(mock_client)

    assert len(tasks) == 1
    assert tasks[0]["id"] == "task-1"
    assert tasks[0]["task_type"] == "cohort_query"


@pytest.mark.asyncio
async def test_poll_for_tasks_empty():
    """Should return empty list when no tasks."""
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = []
    mock_client.get.return_value = mock_response

    tasks = await _poll_for_tasks(mock_client)

    assert tasks == []


@pytest.mark.asyncio
async def test_poll_for_tasks_error():
    """Should return empty list on error."""
    mock_client = AsyncMock()
    mock_client.get.side_effect = Exception("Connection refused")

    tasks = await _poll_for_tasks(mock_client)

    assert tasks == []


@pytest.mark.asyncio
async def test_poll_for_tasks_non_200():
    """Should return empty list on non-200 status."""
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_client.get.return_value = mock_response

    tasks = await _poll_for_tasks(mock_client)

    assert tasks == []


# ---- _send_heartbeat ----


@pytest.mark.asyncio
async def test_send_heartbeat_success():
    """Should POST to the heartbeat endpoint."""
    mock_client = AsyncMock()

    await _send_heartbeat(mock_client)

    mock_client.post.assert_called_once()
    call_args = mock_client.post.call_args
    assert "/heartbeat" in call_args[0][0]


@pytest.mark.asyncio
async def test_send_heartbeat_error():
    """Should not raise on error."""
    mock_client = AsyncMock()
    mock_client.post.side_effect = Exception("Network error")

    # Should not raise
    await _send_heartbeat(mock_client)


# ---- _report_task_result ----


@pytest.mark.asyncio
async def test_report_task_result_success():
    """Should POST result to hub."""
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response

    await _report_task_result(mock_client, "task-123", {"success": True, "result": "data"})

    mock_client.post.assert_called_once()
    call_args = mock_client.post.call_args
    assert "task-123" in call_args[0][0]
    assert call_args[1]["json"]["success"] is True


@pytest.mark.asyncio
async def test_report_task_result_error():
    """Should not raise on error, retries then gives up."""
    mock_client = AsyncMock()
    mock_client.post.side_effect = Exception("Network error")

    # Should not raise
    await _report_task_result(mock_client, "task-123", {"success": True})
    # Should have retried 3 times
    assert mock_client.post.call_count == 3


@pytest.mark.asyncio
async def test_report_task_result_non_200_retries_then_gives_up():
    """Should retry on non-200 status and give up after max retries."""
    mock_response = AsyncMock()
    mock_response.status_code = 500
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response

    with patch("trust_api.services.task_poller.asyncio.sleep", new_callable=AsyncMock):
        await _report_task_result(mock_client, "task-123", {"success": True})

    assert mock_client.post.call_count == 3


@pytest.mark.asyncio
async def test_report_task_result_includes_error_in_result():
    """Should include error details in result field for failed tasks."""
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response

    await _report_task_result(
        mock_client, "task-123",
        {"success": False, "error": "Something went wrong"},
    )

    call_args = mock_client.post.call_args
    payload = call_args[1]["json"]
    assert payload["success"] is False
    assert "Something went wrong" in payload["result"]


# ---- _process_task ----


@pytest.mark.asyncio
async def test_process_task_dispatches_cohort_query():
    """Should dispatch to the correct handler based on task_type."""
    with (
        patch("trust_api.services.task_poller.TASK_HANDLERS") as mock_handlers,
        patch("trust_api.services.task_poller.decrypt", side_effect=lambda x: x),
    ):
        mock_handler = AsyncMock(return_value={"success": True})
        mock_handlers.get.return_value = mock_handler

        task = {
            "id": "task-1",
            "task_type": "cohort_query",
            "payload": '{"query": "SELECT 1"}',
        }

        result = await _process_task(task)

        assert result["success"] is True
        mock_handler.assert_called_once_with({"query": "SELECT 1"})


@pytest.mark.asyncio
async def test_process_task_unknown_type():
    """Should return failure for unknown task type."""
    task = {
        "id": "task-1",
        "task_type": "unknown_type",
        "payload": "{}",
    }

    result = await _process_task(task)

    assert result["success"] is False
    assert "Unknown task type" in result["error"]


@pytest.mark.asyncio
async def test_process_task_invalid_payload():
    """Should return failure for invalid JSON payload."""
    with (
        patch("trust_api.services.task_poller.TASK_HANDLERS") as mock_handlers,
        patch("trust_api.services.task_poller.decrypt", side_effect=lambda x: x),
    ):
        mock_handlers.get.return_value = AsyncMock()

        task = {
            "id": "task-1",
            "task_type": "cohort_query",
            "payload": "not valid json{{{",
        }

        result = await _process_task(task)

        assert result["success"] is False
        assert "Invalid payload" in result["error"]


# ---- run_poller (integration) ----


@pytest.mark.asyncio
async def test_run_poller_processes_tasks_and_reports_results():
    """Should poll for tasks, process them, report results, then loop."""
    call_count = 0

    async def fake_sleep(seconds):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            raise asyncio.CancelledError()  # Break the infinite loop after one iteration

    with (
        patch("trust_api.services.task_poller._send_heartbeat", new_callable=AsyncMock) as mock_heartbeat,
        patch("trust_api.services.task_poller._poll_for_tasks", new_callable=AsyncMock) as mock_poll,
        patch("trust_api.services.task_poller._process_task", new_callable=AsyncMock) as mock_process,
        patch("trust_api.services.task_poller._report_task_result", new_callable=AsyncMock) as mock_report,
        patch("trust_api.services.task_poller.asyncio.sleep", side_effect=fake_sleep),
    ):
        mock_poll.return_value = [
            {"id": "task-1", "task_type": "cohort_query", "payload": "{}"},
        ]
        mock_process.return_value = {"success": True}

        with pytest.raises(asyncio.CancelledError):
            await run_poller()

        mock_heartbeat.assert_called_once()
        mock_poll.assert_called_once()
        mock_process.assert_called_once()
        mock_report.assert_called_once_with(mock_report.call_args[0][0], "task-1", {"success": True})


@pytest.mark.asyncio
async def test_run_poller_reports_error_on_task_exception():
    """Should report error result when a task processing raises an exception."""
    call_count = 0

    async def fake_sleep(seconds):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            raise asyncio.CancelledError()

    with (
        patch("trust_api.services.task_poller._send_heartbeat", new_callable=AsyncMock),
        patch("trust_api.services.task_poller._poll_for_tasks", new_callable=AsyncMock) as mock_poll,
        patch(
            "trust_api.services.task_poller._process_task",
            new_callable=AsyncMock,
            side_effect=Exception("handler crashed"),
        ),
        patch("trust_api.services.task_poller._report_task_result", new_callable=AsyncMock) as mock_report,
        patch("trust_api.services.task_poller.asyncio.sleep", side_effect=fake_sleep),
    ):
        mock_poll.return_value = [{"id": "task-1", "task_type": "cohort_query", "payload": "{}"}]

        with pytest.raises(asyncio.CancelledError):
            await run_poller()

        # Should have reported an error result
        mock_report.assert_called_once()
        result_arg = mock_report.call_args[0][2]
        assert result_arg["success"] is False
        assert "handler crashed" in result_arg["error"]


@pytest.mark.asyncio
async def test_run_poller_continues_on_polling_error():
    """Should catch errors in the polling loop and continue."""
    call_count = 0

    async def fake_sleep(seconds):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            raise asyncio.CancelledError()

    with (
        patch(
            "trust_api.services.task_poller._send_heartbeat",
            new_callable=AsyncMock,
            side_effect=Exception("heartbeat failed"),
        ),
        patch("trust_api.services.task_poller.asyncio.sleep", side_effect=fake_sleep),
    ):
        # Should not raise (exception is caught), but CancelledError from sleep breaks the loop
        with pytest.raises(asyncio.CancelledError):
            await run_poller()
