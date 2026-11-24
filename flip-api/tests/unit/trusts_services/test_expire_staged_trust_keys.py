# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

"""The scheduled expiry of staged trust keys (FLIP#1714). Against real Postgres in test_register_trust_db_flow."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

from flip_api.db.models.main_models import TrustsAudit
from flip_api.domain.schemas.actions import TrustAuditAction
from flip_api.trusts_services.expire_staged_trust_keys import (
    expire_staged_trust_keys,
    expire_staged_trust_keys_scheduled_task,
)


def test_audits_each_row_the_update_changed_and_commits_once():
    db = MagicMock()
    rows = [(uuid4(), "GSTT"), (uuid4(), "UCLH")]
    db.execute.return_value.all.return_value = rows

    assert expire_staged_trust_keys(db, now=datetime.now(timezone.utc)) == 2

    audits = [c.args[0] for c in db.add.call_args_list if isinstance(c.args[0], TrustsAudit)]
    assert [(a.trust_id, a.action) for a in audits] == [
        (rows[0][0], TrustAuditAction.KEY_EXPIRED),
        (rows[1][0], TrustAuditAction.KEY_EXPIRED),
    ]
    db.commit.assert_called_once()


def test_the_update_is_conditional_on_the_deadline_at_write_time():
    """Rows are chosen by the UPDATE's own WHERE, not from rows read earlier, so a re-staged key is left alone."""
    db = MagicMock()
    db.execute.return_value.all.return_value = []

    expire_staged_trust_keys(db)

    sql = str(db.execute.call_args.args[0])
    assert sql.startswith("UPDATE trust SET pending_api_key_hash")
    assert "pending_api_key_expires_at IS NULL OR trust.pending_api_key_expires_at <=" in sql
    assert "RETURNING" in sql


@patch("flip_api.trusts_services.expire_staged_trust_keys.Session")
@patch("flip_api.trusts_services.expire_staged_trust_keys.get_engine")
def test_the_scheduled_task_logs_instead_of_raising(mock_engine, mock_session):
    mock_session.return_value.__enter__.return_value.execute.side_effect = RuntimeError("db down")

    with patch("flip_api.trusts_services.expire_staged_trust_keys.logger") as mock_logger:
        expire_staged_trust_keys_scheduled_task()

    mock_logger.exception.assert_called_once()


def test_the_job_is_registered_with_the_scheduler():
    from flip_api.scheduler.apscheduler_runner import scheduler

    assert expire_staged_trust_keys_scheduled_task in [job.func for job in scheduler.get_jobs()]
