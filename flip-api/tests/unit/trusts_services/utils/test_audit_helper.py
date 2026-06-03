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

from unittest.mock import MagicMock
from uuid import uuid4

from flip_api.db.models.main_models import TrustsAudit
from flip_api.domain.schemas.actions import TrustAuditAction
from flip_api.trusts_services.utils.audit_helper import audit_trust_action


def test_audit_trust_action_persists_row_with_user_id():
    """UI path — the admin's Cognito sub is stamped on the audit row."""
    session = MagicMock()
    trust_id = uuid4()
    user_id = uuid4()

    row = audit_trust_action(
        trust_id=trust_id,
        trust_name="GSTT",
        action=TrustAuditAction.REGISTERED,
        user_id=user_id,
        session=session,
    )

    assert isinstance(row, TrustsAudit)
    assert row.trust_id == trust_id
    assert row.trust_name == "GSTT"
    assert row.action == TrustAuditAction.REGISTERED
    assert row.modified_by_user_id == user_id
    session.add.assert_called_once_with(row)
    session.flush.assert_called_once()
    # Helper must NOT commit — that's the caller's responsibility, so the
    # audit row participates in the surrounding transaction.
    session.commit.assert_not_called()


def test_audit_trust_action_persists_row_with_null_user_for_cli_path():
    """Deploy-CLI path — operator identity lives in CloudTrail, not FLIP users."""
    session = MagicMock()

    row = audit_trust_action(
        trust_id=uuid4(),
        trust_name="GSTT",
        action=TrustAuditAction.DELETED,
        user_id=None,
        session=session,
    )

    assert row.modified_by_user_id is None
    assert row.action == TrustAuditAction.DELETED
