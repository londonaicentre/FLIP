# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
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
"""The cohort drafting override routes (FLIP#1556) are for user managers only, and validate their input."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from flip_api.cohort_services import draft_allowance_override as routes
from flip_api.db.models.user_models import PermissionRef
from flip_api.domain.schemas.cohort_draft import CohortDraftOverrideBody, CohortDraftOverrideClearBody

BODY = CohortDraftOverrideBody(monthly_allowance=60, reason="Benchmark reviewer")


@pytest.mark.parametrize(
    "call",
    [
        lambda db, caller: routes.get_cohort_draft_override(uuid4(), db, caller),
        lambda db, caller: routes.set_cohort_draft_override(uuid4(), BODY, db, caller),
        lambda db, caller: routes.clear_cohort_draft_override(
            uuid4(), CohortDraftOverrideClearBody(reason="r"), db, caller
        ),
    ],
    ids=["get", "set", "clear"],
)
def test_a_caller_without_manage_users_gets_403_and_writes_nothing(call):
    db, caller = MagicMock(), uuid4()
    with patch.object(routes, "has_permissions", return_value=False) as has_permissions:
        with pytest.raises(HTTPException) as raised:
            call(db, caller)
    assert raised.value.status_code == 403
    has_permissions.assert_called_once_with(caller, [PermissionRef.CAN_MANAGE_USERS], db)
    db.add.assert_not_called()
    db.delete.assert_not_called()
    db.commit.assert_not_called()


def test_a_past_expiry_is_refused():
    db = MagicMock()
    body = CohortDraftOverrideBody(
        monthly_allowance=5, expires_at=datetime.now(timezone.utc) - timedelta(minutes=1), reason="r"
    )
    with patch.object(routes, "has_permissions", return_value=True), patch.object(routes, "_require_known_user"):
        with pytest.raises(HTTPException) as raised:
            routes.set_cohort_draft_override(uuid4(), body, db, uuid4())
    assert raised.value.status_code == 400
    db.commit.assert_not_called()


@pytest.mark.parametrize(
    "fields",
    [
        {"monthly_allowance": 5, "reason": ""},
        {"monthly_allowance": 5},
        {"monthly_allowance": -1, "reason": "r"},
        {"monthly_allowance": 5, "monthly_request_cap": -1, "reason": "r"},
    ],
)
def test_a_reason_is_required_and_limits_are_not_negative(fields):
    with pytest.raises(ValidationError):
        CohortDraftOverrideBody(**fields)
