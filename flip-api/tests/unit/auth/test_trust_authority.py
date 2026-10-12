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

import uuid
from unittest.mock import MagicMock, patch

from flip_api.auth.trust_authority import decision_maker_for
from flip_api.db.models.user_models import PermissionRef
from flip_api.domain.schemas.status import DecisionMaker

USER, TRUST = uuid.uuid4(), uuid.uuid4()
MODULE = "flip_api.auth.trust_authority"


@patch(f"{MODULE}.has_trust_permissions", return_value=True)
@patch(f"{MODULE}.has_permissions", return_value=True)
def test_site_run_trust_is_decided_on_the_trust_grant(mock_global, mock_trust):
    db = MagicMock()

    assert decision_maker_for(USER, TRUST, db, has_admin=True) == DecisionMaker.SITE
    mock_trust.assert_called_once_with(USER, [PermissionRef.CAN_APPROVE_FOR_TRUST], TRUST, db)
    mock_global.assert_not_called()


@patch(f"{MODULE}.has_trust_permissions", return_value=False)
@patch(f"{MODULE}.has_permissions", return_value=True)
def test_site_run_trust_refuses_a_hub_admin(mock_global, mock_trust):
    assert decision_maker_for(USER, TRUST, MagicMock(), has_admin=True) is None
    mock_global.assert_not_called()


@patch(f"{MODULE}.has_trust_permissions", return_value=True)
@patch(f"{MODULE}.has_permissions", return_value=True)
def test_hub_run_trust_is_decided_on_the_global_grant(mock_global, mock_trust):
    db = MagicMock()

    assert decision_maker_for(USER, TRUST, db, has_admin=False) == DecisionMaker.HUB
    mock_global.assert_called_once_with(USER, [PermissionRef.CAN_APPROVE_PROJECTS], db)
    mock_trust.assert_not_called()


@patch(f"{MODULE}.has_trust_permissions", return_value=False)
@patch(f"{MODULE}.has_permissions", return_value=False)
def test_hub_run_trust_refuses_a_researcher(mock_global, mock_trust):
    assert decision_maker_for(USER, TRUST, MagicMock(), has_admin=False) is None


@patch(f"{MODULE}.trusts_with_admin", return_value={TRUST})
@patch(f"{MODULE}.has_trust_permissions", return_value=True)
def test_whether_the_trust_has_an_admin_is_looked_up_when_not_given(mock_trust, mock_with_admin):
    db = MagicMock()

    assert decision_maker_for(USER, TRUST, db) == DecisionMaker.SITE
    mock_with_admin.assert_called_once_with([TRUST], db)
