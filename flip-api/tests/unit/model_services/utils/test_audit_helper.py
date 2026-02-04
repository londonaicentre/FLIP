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

import pytest

from flip_api.domain.interfaces.model import IModelAuditAction
from flip_api.domain.schemas.actions import ModelAuditAction
from flip_api.model_services.utils.audit_helper import audit_model_action, audit_model_actions


def test_audit_model_action():
    session = MagicMock()
    model_id = uuid4()
    action = ModelAuditAction.EDIT
    user_id = "test_user"

    session.add.side_effect = lambda x: None
    session.commit.side_effect = lambda: None
    session.refresh.side_effect = lambda x: x

    result = audit_model_action(model_id, action, user_id, session)

    session.add.assert_called_once()

    assert result.model_id == model_id
    assert result.action == action
    assert result.user_id == user_id


def test_audit_model_actions_success():
    session = MagicMock()
    actions = [
        IModelAuditAction(model_id=uuid4(), action=ModelAuditAction.EDIT, userid="user1"),
        IModelAuditAction(model_id=uuid4(), action=ModelAuditAction.DELETE, userid="user2"),
    ]

    session.add_all.side_effect = lambda x: None
    session.commit.side_effect = lambda: None
    session.refresh.side_effect = lambda x: x

    result = audit_model_actions(actions, session)

    session.add_all.assert_called_once()
    session.commit.assert_called_once()
    assert len(result) == 2
    for i, entry in enumerate(result):
        assert entry.model_id == actions[i].model_id
        assert entry.action == actions[i].action
        assert entry.user_id == actions[i].userid


def test_audit_model_actions_empty_list():
    session = MagicMock()
    with pytest.raises(RuntimeError, match="Unable to create audit"):
        audit_model_actions([], session)
