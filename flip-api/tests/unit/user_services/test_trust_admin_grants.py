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
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from flip_api.db.models.main_models import Trust
from flip_api.db.models.user_models import RoleRef
from flip_api.user_services.trust_admin_grants import resolve_role_grants

TRUST_ID = uuid.uuid4()


def _db(trust_exists: bool = True) -> MagicMock:
    db = MagicMock()
    db.get.side_effect = lambda model, key: MagicMock(spec=Trust, id=key, name="T") if trust_exists else None
    return db


def test_trust_admin_becomes_global_researcher_plus_the_trust():
    grants = resolve_role_grants([RoleRef.TRUST_ADMIN.value], TRUST_ID, _db())

    assert grants.global_role_ids == [RoleRef.RESEARCHER.value]
    assert grants.trust_admin_trust_id == TRUST_ID


def test_trust_admin_without_a_trust_is_refused():
    with pytest.raises(HTTPException) as exc:
        resolve_role_grants([RoleRef.TRUST_ADMIN.value], None, _db())

    assert exc.value.status_code == 400


def test_trust_admin_must_be_the_only_role():
    with pytest.raises(HTTPException) as exc:
        resolve_role_grants([RoleRef.TRUST_ADMIN.value, RoleRef.ADMIN.value], TRUST_ID, _db())

    assert exc.value.status_code == 400


def test_trust_admin_at_an_unknown_trust_is_404():
    with pytest.raises(HTTPException) as exc:
        resolve_role_grants([RoleRef.TRUST_ADMIN.value], TRUST_ID, _db(trust_exists=False))

    assert exc.value.status_code == 404


def test_a_trust_named_with_any_other_role_is_refused():
    with pytest.raises(HTTPException) as exc:
        resolve_role_grants([RoleRef.RESEARCHER.value], TRUST_ID, _db())

    assert exc.value.status_code == 400


def test_other_roles_pass_through_unchanged():
    grants = resolve_role_grants([RoleRef.VIEWER.value], None, _db())

    assert grants.global_role_ids == [RoleRef.VIEWER.value]
    assert grants.trust_admin_trust_id is None
