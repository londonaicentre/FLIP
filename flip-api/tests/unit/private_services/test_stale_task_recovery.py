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

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from flip_api.db.models.main_models import TrustTask
from flip_api.domain.schemas.status import TaskStatus, TaskType
from flip_api.private_services.stale_task_recovery import (
    recover_stale_tasks,
    recover_stale_tasks_scheduled_task,
    retry_failed_post_processing,
)


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def stale_task():
    """A task that's been IN_PROGRESS for longer than the timeout."""
    task = MagicMock(spec=TrustTask)
    task.id = uuid4()
    task.trust_id = uuid4()
    task.task_type = TaskType.COHORT_QUERY
    task.status = TaskStatus.IN_PROGRESS
    task.retry_count = 0
    task.updated_at = datetime.now(timezone.utc) - timedelta(minutes=60)
    return task


@patch("flip_api.private_services.stale_task_recovery.get_settings")
def test_recover_stale_tasks_resets_to_pending(mock_settings, mock_db, stale_task):
    """Stale IN_PROGRESS tasks should be reset to PENDING and retry_count incremented."""
    mock_settings.return_value.TASK_STALE_TIMEOUT_MINUTES = 30
    mock_settings.return_value.TASK_MAX_RETRIES = 3
    mock_db.exec.return_value.all.return_value = [stale_task]

    count = recover_stale_tasks(mock_db)

    assert count == 1
    assert stale_task.status == TaskStatus.PENDING
    assert stale_task.retry_count == 1
    assert stale_task.updated_at is not None
    assert mock_db.commit.called


@patch("flip_api.private_services.stale_task_recovery.get_settings")
def test_recover_stale_tasks_no_stale(mock_settings, mock_db):
    """Should return 0 when no stale tasks found."""
    mock_settings.return_value.TASK_STALE_TIMEOUT_MINUTES = 30
    mock_settings.return_value.TASK_MAX_RETRIES = 3
    mock_db.exec.return_value.all.return_value = []

    count = recover_stale_tasks(mock_db)

    assert count == 0
    assert not mock_db.commit.called


@patch("flip_api.private_services.stale_task_recovery.get_settings")
def test_recover_stale_tasks_marks_failed_when_max_retries_exceeded(mock_settings, mock_db):
    """Tasks exceeding max retries should be marked FAILED instead of re-queued."""
    mock_settings.return_value.TASK_STALE_TIMEOUT_MINUTES = 30
    mock_settings.return_value.TASK_MAX_RETRIES = 3

    task = MagicMock(spec=TrustTask)
    task.id = uuid4()
    task.trust_id = uuid4()
    task.task_type = TaskType.CREATE_IMAGING
    task.status = TaskStatus.IN_PROGRESS
    task.retry_count = 3  # Already at max
    task.updated_at = datetime.now(timezone.utc) - timedelta(minutes=60)
    mock_db.exec.return_value.all.return_value = [task]

    count = recover_stale_tasks(mock_db)

    assert count == 1
    assert task.status == TaskStatus.FAILED
    assert "maximum retries" in task.result.lower()
    assert mock_db.commit.called


@patch("flip_api.private_services.stale_task_recovery.get_settings")
def test_recover_stale_tasks_mixed_retryable_and_exhausted(mock_settings, mock_db):
    """Should handle a mix of retryable and exhausted tasks correctly."""
    mock_settings.return_value.TASK_STALE_TIMEOUT_MINUTES = 30
    mock_settings.return_value.TASK_MAX_RETRIES = 2

    retryable_task = MagicMock(spec=TrustTask)
    retryable_task.id = uuid4()
    retryable_task.task_type = TaskType.COHORT_QUERY
    retryable_task.status = TaskStatus.IN_PROGRESS
    retryable_task.retry_count = 1
    retryable_task.updated_at = datetime.now(timezone.utc) - timedelta(minutes=60)

    exhausted_task = MagicMock(spec=TrustTask)
    exhausted_task.id = uuid4()
    exhausted_task.task_type = TaskType.CREATE_IMAGING
    exhausted_task.status = TaskStatus.IN_PROGRESS
    exhausted_task.retry_count = 2
    exhausted_task.updated_at = datetime.now(timezone.utc) - timedelta(minutes=60)

    mock_db.exec.return_value.all.return_value = [retryable_task, exhausted_task]

    count = recover_stale_tasks(mock_db)

    assert count == 2
    assert retryable_task.status == TaskStatus.PENDING
    assert retryable_task.retry_count == 2
    assert exhausted_task.status == TaskStatus.FAILED
    assert mock_db.commit.called


@patch("flip_api.private_services.imaging_notifications.handle_imaging_task_completed")
def test_retry_failed_post_processing_success(mock_handle, mock_db):
    """Should retry post-processing for tasks with needs_post_processing=True."""
    task = MagicMock(spec=TrustTask)
    task.id = uuid4()
    task.status = TaskStatus.COMPLETED
    task.task_type = TaskType.CREATE_IMAGING
    task.needs_post_processing = True
    mock_db.exec.return_value.all.return_value = [task]

    count = retry_failed_post_processing(mock_db)

    assert count == 1
    mock_handle.assert_called_once_with(task, mock_db)
    assert task.needs_post_processing is False
    assert mock_db.commit.called


@patch("flip_api.private_services.imaging_notifications.handle_imaging_task_completed")
def test_retry_failed_post_processing_failure(mock_handle, mock_db):
    """Should handle post-processing retry failure gracefully."""
    mock_handle.side_effect = Exception("SES unavailable")

    task = MagicMock(spec=TrustTask)
    task.id = uuid4()
    task.status = TaskStatus.COMPLETED
    task.task_type = TaskType.CREATE_IMAGING
    task.needs_post_processing = True
    mock_db.exec.return_value.all.return_value = [task]

    count = retry_failed_post_processing(mock_db)

    assert count == 0
    assert mock_db.rollback.called


def test_retry_failed_post_processing_none_pending(mock_db):
    """Should return 0 when no tasks need post-processing."""
    mock_db.exec.return_value.all.return_value = []

    count = retry_failed_post_processing(mock_db)

    assert count == 0


# ---- recover_stale_tasks_scheduled_task ----


@patch("flip_api.private_services.stale_task_recovery.retry_failed_post_processing")
@patch("flip_api.private_services.stale_task_recovery.recover_stale_tasks")
@patch("flip_api.private_services.stale_task_recovery.Session")
def test_recover_stale_tasks_scheduled_task_calls_both_functions(mock_session_cls, mock_recover, mock_retry):
    """Should open a session and call both recovery functions."""
    mock_db = MagicMock()
    mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_db)
    mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

    recover_stale_tasks_scheduled_task()

    mock_recover.assert_called_once_with(mock_db)
    mock_retry.assert_called_once_with(mock_db)


@patch("flip_api.private_services.stale_task_recovery.Session")
def test_recover_stale_tasks_scheduled_task_handles_exception(mock_session_cls):
    """Should catch and log exceptions without raising."""
    mock_session_cls.return_value.__enter__ = MagicMock(side_effect=Exception("DB down"))
    mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

    # Should not raise
    recover_stale_tasks_scheduled_task()
