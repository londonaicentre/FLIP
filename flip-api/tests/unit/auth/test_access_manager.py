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

import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, status
from sqlmodel import Session

from flip_api.auth import trust_key_cache
from flip_api.auth.access_manager import (
    authenticate_trust,
    can_access_project,
    can_contribute_to_project,
    can_modify_model,
    can_modify_project,
)
from flip_api.db.models.main_models import Trust, TrustsAudit
from flip_api.domain.schemas.actions import TrustAuditAction

VALID_TEST_KEY = "test_secret_key_12345_valid"
VALID_TEST_KEY_HASH = hashlib.sha256(VALID_TEST_KEY.encode()).hexdigest()
WRONG_TEST_KEY = "wrong_secret_key_67890_invalid"
TRUST_NAME = "Trust_1"

PATCH_HAS_PERMISSIONS = "flip_api.auth.access_manager.has_permissions"
PATCH_CAN_MODIFY_PROJECT = "flip_api.auth.access_manager.can_modify_project"
PATCH_CAN_CONTRIBUTE_TO_PROJECT = "flip_api.auth.access_manager.can_contribute_to_project"
PATCH_GET_SETTINGS = "flip_api.auth.access_manager.get_settings"


def _db_with_trust_rows(rows: list[Trust]) -> MagicMock:
    """Build a Session mock whose ``exec(...).all()`` returns the given Trust rows.

    Mirrors ``authenticate_trust``'s query —
    ``select(Trust).where(api_key_hash IS NOT NULL)``. Rows without an
    api_key_hash are filtered here just like the real SQL does so unit tests
    exercise the same candidate set the production query yields.
    """
    db = MagicMock(spec=Session)
    db.exec.return_value.all.return_value = [t for t in rows if t.api_key_hash is not None]
    db.get.return_value = None  # default: cache-hit path treated as stale
    db.execute.side_effect = _conditional_update_on(rows)
    return db


def _conditional_update_on(rows: list[Trust]):
    """Apply the promotion's conditional ``UPDATE`` to ``rows`` the way Postgres would.

    Reads the statement's bound parameters, so a row is changed only while its id, staged hash and
    unexpired deadline all still match at "write time", and returns the ``rowcount`` the code reads.
    """

    def execute(statement):
        params = statement.compile().params
        changed = 0
        for row in rows:
            deadline = row.pending_api_key_expires_at
            if (
                row.id == params["id_1"]
                and row.pending_api_key_hash == params["pending_api_key_hash_1"]
                and deadline is not None
                and deadline.replace(tzinfo=None) > params["pending_api_key_expires_at_1"]
            ):
                row.api_key_hash = params["api_key_hash"]
                row.pending_api_key_hash = None
                row.pending_api_key_expires_at = None
                changed += 1
        return MagicMock(rowcount=changed)

    return execute


@pytest.fixture(autouse=True)
def _clear_trust_key_cache():
    """Reset the trust-auth in-process cache before every test in this module.

    Without this, an earlier test that populated the cache (e.g. via a
    successful authenticate_trust call) would leak its entry into the next
    test, making the cache-stale and cache-hit assertions order-dependent.
    """
    trust_key_cache.invalidate()
    yield
    trust_key_cache.invalidate()


class TestAuthenticateTrust:
    def test_valid_api_key_returns_trust_row(self):
        """The dependency returns the resolved Trust row (id + name + everything)."""
        trust = Trust(id=uuid4(), name=TRUST_NAME, api_key_hash=VALID_TEST_KEY_HASH)
        db = _db_with_trust_rows([trust])

        result = authenticate_trust(api_key=VALID_TEST_KEY, db=db)

        assert result is trust
        assert result.name == TRUST_NAME

    def test_invalid_api_key_returns_401(self):
        db = _db_with_trust_rows([Trust(id=uuid4(), name=TRUST_NAME, api_key_hash=VALID_TEST_KEY_HASH)])
        with pytest.raises(HTTPException) as exc_info:
            authenticate_trust(api_key=WRONG_TEST_KEY, db=db)
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert exc_info.value.detail == "Invalid API Key."

    def test_missing_api_key_returns_401(self):
        db = _db_with_trust_rows([])
        with pytest.raises(HTTPException) as exc_info:
            authenticate_trust(api_key=None, db=db)
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert exc_info.value.detail == "Not authenticated: API key is missing."

    def test_empty_api_key_returns_401(self):
        db = _db_with_trust_rows([])
        with pytest.raises(HTTPException) as exc_info:
            authenticate_trust(api_key="", db=db)
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert exc_info.value.detail == "Not authenticated: API key is missing."

    def test_no_trust_rows_returns_401(self):
        """A fresh hub with no registered trusts rejects every key."""
        db = _db_with_trust_rows([])
        with pytest.raises(HTTPException) as exc_info:
            authenticate_trust(api_key=VALID_TEST_KEY, db=db)
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    def test_arbitrary_display_name_resolves_through_key(self):
        """A trust with spaces and parens in its name authenticates by key alone."""
        named_trust = Trust(
            id=uuid4(),
            name="(Mock) Guys and St Thomas NHS Trust",
            api_key_hash=VALID_TEST_KEY_HASH,
        )
        db = _db_with_trust_rows([named_trust])

        result = authenticate_trust(api_key=VALID_TEST_KEY, db=db)

        assert result.name == "(Mock) Guys and St Thomas NHS Trust"

    def test_walks_all_candidates_to_avoid_timing_side_channel(self):
        """The implementation must not short-circuit on first non-match — every candidate
        is checked so total comparison time is independent of which trust matched.
        """
        trusts = [
            Trust(id=uuid4(), name=f"T_{i}", api_key_hash=hashlib.sha256(f"k{i}".encode()).hexdigest())
            for i in range(5)
        ]
        trusts.append(Trust(id=uuid4(), name="match", api_key_hash=VALID_TEST_KEY_HASH))
        db = _db_with_trust_rows(trusts)

        result = authenticate_trust(api_key=VALID_TEST_KEY, db=db)

        assert result.name == "match"

    def test_cache_hit_skips_candidate_sweep(self):
        """After a successful auth, the next call resolves via the in-process cache:
        a single ``db.get`` PK fetch, no ``db.exec`` sweep. Locks in the perf path.
        """
        trust = Trust(id=uuid4(), name=TRUST_NAME, api_key_hash=VALID_TEST_KEY_HASH)
        db = _db_with_trust_rows([trust])

        first = authenticate_trust(api_key=VALID_TEST_KEY, db=db)
        assert first is trust
        sweep_calls_after_first = db.exec.call_count

        db.get.return_value = trust  # cache hit fetches via PK
        second = authenticate_trust(api_key=VALID_TEST_KEY, db=db)

        assert second is trust
        assert db.exec.call_count == sweep_calls_after_first  # no new sweep
        db.get.assert_called_once_with(Trust, trust.id)

    def test_cache_stale_after_delete_falls_through_to_sweep_and_401s(self):
        """If the cached trust id has since been deleted (db.get returns None), the
        request must not 401 immediately on the cache hit — it must fall through to
        the full sweep, which finds no match, and only THEN 401.
        """
        trust = Trust(id=uuid4(), name=TRUST_NAME, api_key_hash=VALID_TEST_KEY_HASH)
        db = _db_with_trust_rows([trust])
        authenticate_trust(api_key=VALID_TEST_KEY, db=db)  # populate cache

        empty_db = _db_with_trust_rows([])
        empty_db.get.return_value = None  # cached id no longer resolves

        with pytest.raises(HTTPException) as exc_info:
            authenticate_trust(api_key=VALID_TEST_KEY, db=empty_db)
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        empty_db.exec.assert_called()  # sweep ran


NEW_TEST_KEY = "rotated_secret_key_24680_new"
NEW_TEST_KEY_HASH = hashlib.sha256(NEW_TEST_KEY.encode()).hexdigest()


def _audit_actions(db: MagicMock) -> list[TrustAuditAction]:
    return [c.args[0].action for c in db.add.call_args_list if isinstance(c.args[0], TrustsAudit)]


def _staged_trust(expires_in: timedelta = timedelta(days=14)) -> Trust:
    return Trust(
        id=uuid4(),
        name=TRUST_NAME,
        api_key_hash=VALID_TEST_KEY_HASH,
        pending_api_key_hash=NEW_TEST_KEY_HASH,
        pending_api_key_expires_at=datetime.now(timezone.utc) + expires_in,
    )


class TestStagedKeyRotation:
    """FLIP#1714: a rotated key is staged next to the live one until the trust first uses it."""

    def test_old_key_still_works_while_a_new_key_is_staged(self):
        trust = _staged_trust()
        db = _db_with_trust_rows([trust])

        assert authenticate_trust(api_key=VALID_TEST_KEY, db=db) is trust

        assert trust.api_key_hash == VALID_TEST_KEY_HASH
        assert trust.pending_api_key_hash == NEW_TEST_KEY_HASH
        assert _audit_actions(db) == []

    def test_first_use_of_the_new_key_promotes_it_and_audits(self):
        trust = _staged_trust()
        db = _db_with_trust_rows([trust])

        assert authenticate_trust(api_key=NEW_TEST_KEY, db=db) is trust

        assert trust.api_key_hash == NEW_TEST_KEY_HASH
        assert trust.pending_api_key_hash is None
        assert trust.pending_api_key_expires_at is None
        assert _audit_actions(db) == [TrustAuditAction.KEY_PROMOTED]
        db.commit.assert_called()

    def test_old_key_is_refused_after_promotion(self):
        trust = _staged_trust()
        db = _db_with_trust_rows([trust])
        authenticate_trust(api_key=NEW_TEST_KEY, db=db)

        with pytest.raises(HTTPException) as exc_info:
            authenticate_trust(api_key=VALID_TEST_KEY, db=_db_with_trust_rows([trust]))
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    def test_an_old_key_cached_before_promotion_is_refused_after_it(self):
        """Another worker may still hold the old key in its cache: the live-hash re-check refuses it."""
        trust = _staged_trust()
        db = _db_with_trust_rows([trust])
        authenticate_trust(api_key=VALID_TEST_KEY, db=db)  # old key cached
        authenticate_trust(api_key=NEW_TEST_KEY, db=db)  # promotion
        trust_key_cache.remember(VALID_TEST_KEY_HASH, trust.id)  # a worker that never saw the invalidation

        db_after = _db_with_trust_rows([trust])
        db_after.get.return_value = trust  # the cache hit resolves the row
        with pytest.raises(HTTPException) as exc_info:
            authenticate_trust(api_key=VALID_TEST_KEY, db=db_after)
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    def test_new_key_cached_hit_also_promotes(self):
        """A cache entry for the pending key (made by another path) still promotes on use."""
        trust = _staged_trust()
        trust_key_cache.remember(NEW_TEST_KEY_HASH, trust.id)
        db = _db_with_trust_rows([trust])
        db.get.return_value = trust

        assert authenticate_trust(api_key=NEW_TEST_KEY, db=db) is trust
        assert trust.api_key_hash == NEW_TEST_KEY_HASH
        assert _audit_actions(db) == [TrustAuditAction.KEY_PROMOTED]

    def test_a_pending_key_past_its_deadline_is_refused_and_nothing_is_written(self):
        """Expiry is the scheduler's job: the auth path refuses the key but writes nothing (FLIP#1714)."""
        trust = _staged_trust(expires_in=timedelta(seconds=-1))
        db = _db_with_trust_rows([trust])

        with pytest.raises(HTTPException) as exc_info:
            authenticate_trust(api_key=NEW_TEST_KEY, db=db)
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert trust.api_key_hash == VALID_TEST_KEY_HASH
        assert _audit_actions(db) == []
        db.execute.assert_not_called()
        db.commit.assert_not_called()

    def test_the_old_key_carries_on_after_the_pending_key_expires(self):
        trust = _staged_trust(expires_in=timedelta(seconds=-1))
        db = _db_with_trust_rows([trust])

        assert authenticate_trust(api_key=VALID_TEST_KEY, db=db) is trust
        assert _audit_actions(db) == []
        db.commit.assert_not_called()

    def test_a_pending_key_with_no_deadline_is_refused(self):
        """Every staged key is written with a deadline; a row without one is not accepted for auth."""
        trust = _staged_trust()
        trust.pending_api_key_expires_at = None
        db = _db_with_trust_rows([trust])

        with pytest.raises(HTTPException) as exc_info:
            authenticate_trust(api_key=NEW_TEST_KEY, db=db)
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert trust.api_key_hash == VALID_TEST_KEY_HASH

    def test_two_first_calls_with_the_new_key_write_one_promotion(self):
        """trust-api's heartbeat and task poll after a restart: only the call that wins the update audits."""
        trust = _staged_trust()
        db = _db_with_trust_rows([trust])

        assert authenticate_trust(api_key=NEW_TEST_KEY, db=db) is trust
        trust_key_cache.invalidate()  # the second call comes from another worker, through the sweep
        assert authenticate_trust(api_key=NEW_TEST_KEY, db=db) is trust

        assert _audit_actions(db) == [TrustAuditAction.KEY_PROMOTED]

    def test_a_promotion_that_loses_to_a_revoke_is_refused(self):
        """A cutover that commits between the sweep's read and the promotion wins; the staged key gets 401."""
        trust = _staged_trust()
        db = _db_with_trust_rows([trust])
        revoked_hash = hashlib.sha256(b"revoke-old key").hexdigest()

        def revoke_then_update(statement):
            # The --revoke-old commit lands first, as it would from another process.
            trust.api_key_hash, trust.pending_api_key_hash, trust.pending_api_key_expires_at = revoked_hash, None, None
            return _conditional_update_on([trust])(statement)

        db.execute.side_effect = revoke_then_update

        with pytest.raises(HTTPException) as exc_info:
            authenticate_trust(api_key=NEW_TEST_KEY, db=db)
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert trust.api_key_hash == revoked_hash
        assert _audit_actions(db) == []

    def test_a_naive_deadline_from_the_database_is_read_as_utc(self):
        """The column is ``timestamp without time zone``; a naive value must not raise on compare."""
        trust = _staged_trust()
        trust.pending_api_key_expires_at = (datetime.now(timezone.utc) + timedelta(days=1)).replace(tzinfo=None)
        db = _db_with_trust_rows([trust])

        assert authenticate_trust(api_key=NEW_TEST_KEY, db=db) is trust
        assert trust.api_key_hash == NEW_TEST_KEY_HASH

    def test_a_pending_key_of_one_trust_never_authenticates_another(self):
        staged = _staged_trust()
        other = Trust(id=uuid4(), name="Other", api_key_hash=hashlib.sha256(b"other").hexdigest())
        db = _db_with_trust_rows([other, staged])

        assert authenticate_trust(api_key=NEW_TEST_KEY, db=db) is staged
        assert other.api_key_hash == hashlib.sha256(b"other").hexdigest()


class TestCanModifyProject:
    def test_returns_true_when_user_has_manage_projects_permission(self):
        user_id = uuid4()
        project_id = uuid4()
        db = MagicMock(spec=Session)

        with patch(PATCH_HAS_PERMISSIONS, return_value=True):
            result = can_modify_project(user_id, project_id, db)

        assert result is True
        db.exec.assert_not_called()

    def test_returns_true_when_user_is_project_owner(self):
        user_id = uuid4()
        project_id = uuid4()
        db = MagicMock(spec=Session)
        mock_project = MagicMock()
        mock_project.owner_id = user_id
        db.exec.return_value.first.return_value = mock_project

        with patch(PATCH_HAS_PERMISSIONS, return_value=False):
            result = can_modify_project(user_id, project_id, db)

        assert result is True

    def test_returns_false_when_user_is_not_project_owner(self):
        user_id = uuid4()
        project_id = uuid4()
        db = MagicMock(spec=Session)
        mock_project = MagicMock()
        mock_project.owner_id = uuid4()
        db.exec.return_value.first.return_value = mock_project

        with patch(PATCH_HAS_PERMISSIONS, return_value=False):
            result = can_modify_project(user_id, project_id, db)

        assert result is False

    def test_returns_false_when_project_not_found(self):
        user_id = uuid4()
        project_id = uuid4()
        db = MagicMock(spec=Session)
        db.exec.return_value.first.return_value = None

        with patch(PATCH_HAS_PERMISSIONS, return_value=False):
            result = can_modify_project(user_id, project_id, db)

        assert result is False


class TestCanAccessProject:
    def test_admin_with_manage_projects_permission_bypasses_ownership_check(self):
        """Admin (has CAN_MANAGE_PROJECTS) gets access without an ownership check."""
        user_id = uuid4()
        project_id = uuid4()
        db = MagicMock(spec=Session)

        with patch(PATCH_HAS_PERMISSIONS, return_value=True):
            result = can_access_project(user_id, project_id, db)

        assert result is True
        db.exec.assert_not_called()

    def test_researcher_without_manage_projects_can_access_owned_project(self):
        """Researcher (no CAN_MANAGE_PROJECTS) can access projects via ownership / ProjectUserAccess."""
        user_id = uuid4()
        project_id = uuid4()
        db = MagicMock(spec=Session)
        db.exec.return_value.first.return_value = project_id

        with patch(PATCH_HAS_PERMISSIONS, return_value=False):
            result = can_access_project(user_id, project_id, db)

        assert result is True

    def test_researcher_without_manage_projects_denied_for_unrelated_project(self):
        """Researcher cannot see projects they neither own nor are a member of — this is the issue #358 fix."""
        user_id = uuid4()
        project_id = uuid4()
        db = MagicMock(spec=Session)
        db.exec.return_value.first.return_value = None

        with patch(PATCH_HAS_PERMISSIONS, return_value=False):
            result = can_access_project(user_id, project_id, db)

        assert result is False


class TestCanContributeToProject:
    """Tests for can_contribute_to_project — looser than can_modify_project: allows
    Researcher members of the project to contribute models."""

    def test_admin_with_manage_projects_permission_allowed(self):
        user_id = uuid4()
        project_id = uuid4()
        db = MagicMock(spec=Session)

        with patch(PATCH_HAS_PERMISSIONS, return_value=True):
            result = can_contribute_to_project(user_id, project_id, db)

        assert result is True
        db.exec.assert_not_called()

    def test_project_owner_allowed(self):
        user_id = uuid4()
        project_id = uuid4()
        db = MagicMock(spec=Session)
        mock_project = MagicMock()
        mock_project.owner_id = user_id
        db.exec.return_value.first.return_value = mock_project

        with patch(PATCH_HAS_PERMISSIONS, return_value=False):
            result = can_contribute_to_project(user_id, project_id, db)

        assert result is True

    def test_returns_false_when_project_not_found(self):
        user_id = uuid4()
        project_id = uuid4()
        db = MagicMock(spec=Session)
        db.exec.return_value.first.return_value = None

        with patch(PATCH_HAS_PERMISSIONS, return_value=False):
            result = can_contribute_to_project(user_id, project_id, db)

        assert result is False

    def test_researcher_member_allowed(self):
        """Researcher (CAN_CREATE_PROJECTS) with a ProjectUserAccess row can contribute."""
        user_id = uuid4()
        project_id = uuid4()
        db = MagicMock(spec=Session)
        mock_project = MagicMock()
        mock_project.owner_id = uuid4()
        mock_membership = MagicMock()

        project_result = MagicMock()
        project_result.first.return_value = mock_project
        membership_result = MagicMock()
        membership_result.first.return_value = mock_membership

        def _exec(stmt):
            # Route by what the statement targets — robust to intermediate queries
            # being added in front of these later.
            return membership_result if "project_user_access" in str(stmt).lower() else project_result

        db.exec.side_effect = _exec

        # Admin check first (False), then CAN_CREATE_PROJECTS check (True)
        with patch(PATCH_HAS_PERMISSIONS, side_effect=[False, True]):
            result = can_contribute_to_project(user_id, project_id, db)

        assert result is True

    def test_researcher_non_member_denied(self):
        """Researcher who is not a member of the project cannot contribute."""
        user_id = uuid4()
        project_id = uuid4()
        db = MagicMock(spec=Session)
        mock_project = MagicMock()
        mock_project.owner_id = uuid4()

        project_result = MagicMock()
        project_result.first.return_value = mock_project
        empty_membership_result = MagicMock()
        empty_membership_result.first.return_value = None

        def _exec(stmt):
            return empty_membership_result if "project_user_access" in str(stmt).lower() else project_result

        db.exec.side_effect = _exec

        with patch(PATCH_HAS_PERMISSIONS, side_effect=[False, True]):
            result = can_contribute_to_project(user_id, project_id, db)

        assert result is False

    def test_viewer_member_denied(self):
        """Viewer (no CAN_CREATE_PROJECTS) on a ProjectUserAccess row is still denied."""
        user_id = uuid4()
        project_id = uuid4()
        db = MagicMock(spec=Session)
        mock_project = MagicMock()
        mock_project.owner_id = uuid4()
        db.exec.return_value.first.return_value = mock_project

        # Both permission checks return False (Viewer holds neither permission)
        with patch(PATCH_HAS_PERMISSIONS, return_value=False):
            result = can_contribute_to_project(user_id, project_id, db)

        assert result is False

    def test_viewer_member_does_not_consult_membership(self):
        """Viewer is rejected before the membership query runs.

        Observable effect: only the project lookup hits the DB; no membership query
        is executed. Asserting on db.exec.call_count keeps this resilient to internal
        re-ordering of the permission checks.
        """
        user_id = uuid4()
        project_id = uuid4()
        db = MagicMock(spec=Session)
        mock_project = MagicMock()
        mock_project.owner_id = uuid4()
        db.exec.return_value.first.return_value = mock_project

        with patch(PATCH_HAS_PERMISSIONS, return_value=False):
            can_contribute_to_project(user_id, project_id, db)

        # Project lookup happened, but no follow-up membership query.
        assert db.exec.call_count == 1


class TestCanModifyModel:
    def test_returns_true_when_user_has_manage_projects_permission(self):
        user_id = uuid4()
        model_id = uuid4()
        db = MagicMock(spec=Session)

        with patch(PATCH_HAS_PERMISSIONS, return_value=True):
            result = can_modify_model(user_id, model_id, db)

        assert result is True
        db.exec.assert_not_called()

    def test_returns_false_when_model_not_found(self):
        user_id = uuid4()
        model_id = uuid4()
        db = MagicMock(spec=Session)
        db.exec.return_value.first.return_value = None

        with patch(PATCH_HAS_PERMISSIONS, return_value=False):
            result = can_modify_model(user_id, model_id, db)

        assert result is False

    def test_returns_false_when_model_has_no_project_id(self):
        user_id = uuid4()
        model_id = uuid4()
        db = MagicMock(spec=Session)
        mock_model = MagicMock()
        mock_model.project_id = None
        db.exec.return_value.first.return_value = mock_model

        with patch(PATCH_HAS_PERMISSIONS, return_value=False):
            result = can_modify_model(user_id, model_id, db)

        assert result is False

    def test_returns_true_when_can_modify_project_returns_true(self):
        """Project owner / admin paths short-circuit through can_modify_project."""
        user_id = uuid4()
        model_id = uuid4()
        mock_project_id = uuid4()
        db = MagicMock(spec=Session)
        mock_model = MagicMock()
        mock_model.project_id = mock_project_id
        mock_model.owner_id = uuid4()  # Not the caller — proves the project path is used
        db.exec.return_value.first.return_value = mock_model

        with (
            patch(PATCH_HAS_PERMISSIONS, return_value=False),
            patch(PATCH_CAN_MODIFY_PROJECT, return_value=True) as mock_cmp,
        ):
            result = can_modify_model(user_id, model_id, db)

        assert result is True
        mock_cmp.assert_called_once_with(user_id, mock_project_id, db)

    def test_researcher_member_allowed_for_own_model(self):
        """Researcher member can edit a model where they are Model.owner_id."""
        user_id = uuid4()
        model_id = uuid4()
        mock_project_id = uuid4()
        db = MagicMock(spec=Session)
        mock_model = MagicMock()
        mock_model.project_id = mock_project_id
        mock_model.owner_id = user_id
        db.exec.return_value.first.return_value = mock_model

        with (
            patch(PATCH_HAS_PERMISSIONS, return_value=False),
            patch(PATCH_CAN_MODIFY_PROJECT, return_value=False),
            patch(PATCH_CAN_CONTRIBUTE_TO_PROJECT, return_value=True) as mock_contrib,
        ):
            result = can_modify_model(user_id, model_id, db)

        assert result is True
        mock_contrib.assert_called_once_with(user_id, mock_project_id, db)

    def test_researcher_member_denied_for_other_users_model(self):
        """Researcher member cannot edit another user's model on the same project."""
        user_id = uuid4()
        model_id = uuid4()
        mock_project_id = uuid4()
        db = MagicMock(spec=Session)
        mock_model = MagicMock()
        mock_model.project_id = mock_project_id
        mock_model.owner_id = uuid4()  # Different user owns the model
        db.exec.return_value.first.return_value = mock_model

        with (
            patch(PATCH_HAS_PERMISSIONS, return_value=False),
            patch(PATCH_CAN_MODIFY_PROJECT, return_value=False),
            patch(PATCH_CAN_CONTRIBUTE_TO_PROJECT, return_value=True) as mock_contrib,
        ):
            result = can_modify_model(user_id, model_id, db)

        assert result is False
        # Contribution check is never needed if owner doesn't match.
        mock_contrib.assert_not_called()

    def test_viewer_owning_model_still_denied(self):
        """Defence in depth: even if Model.owner_id matches, a Viewer (not a contributor)
        is rejected."""
        user_id = uuid4()
        model_id = uuid4()
        mock_project_id = uuid4()
        db = MagicMock(spec=Session)
        mock_model = MagicMock()
        mock_model.project_id = mock_project_id
        mock_model.owner_id = user_id
        db.exec.return_value.first.return_value = mock_model

        with (
            patch(PATCH_HAS_PERMISSIONS, return_value=False),
            patch(PATCH_CAN_MODIFY_PROJECT, return_value=False),
            patch(PATCH_CAN_CONTRIBUTE_TO_PROJECT, return_value=False),
        ):
            result = can_modify_model(user_id, model_id, db)

        assert result is False
