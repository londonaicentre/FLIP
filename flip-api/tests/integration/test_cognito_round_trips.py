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

"""Real-Cognito round-trips for ``flip_api.utils.cognito_helpers`` + user_services.

Replaces the all-mocked unit tests for register/delete/update/list users.
moto's ``cognito-idp`` provider intercepts the boto3 calls that
``cognito_helpers`` makes, so the production code path runs end-to-end:
``register_user`` actually creates a user in the moto pool and an audit row
in the Postgres test container; ``delete_user`` actually removes the Cognito
user, drops role grants, and writes audit; ``update_user`` flips the
``enabled`` flag in the pool, queues XNAT trust tasks, and writes audit.
"""

from uuid import UUID, uuid4

import boto3
from fastapi.testclient import TestClient
from sqlmodel import select

from flip_api.db.models.main_models import TaskType, TrustTask
from flip_api.db.models.user_models import RoleRef, UserRole, UsersAudit
from tests.integration.conftest import admin_user, override_verify_token_as


def _admin_create_user(pool_id: str, email: str) -> str:
    """Pre-seed a user in the moto pool and return its ``sub``."""
    cognito = boto3.client("cognito-idp")
    response = cognito.admin_create_user(
        UserPoolId=pool_id,
        Username=email,
        UserAttributes=[{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}],
    )
    sub = next(attr["Value"] for attr in response["User"]["Attributes"] if attr["Name"] == "sub")
    return sub


# ---------------------------------------------------------------------------
# POST /api/users/  (register_user)
# ---------------------------------------------------------------------------


def test_register_user_creates_user_in_pool_and_writes_audit(
    client: TestClient, session, cognito_user_pool
):
    """Happy path: endpoint creates a Cognito user AND writes a 'Registered user' audit row."""
    admin_id = admin_user(session)
    override_verify_token_as(admin_id)

    response = client.post(
        "/api/users/",
        json={"email": "newuser@example.com", "roles": [str(RoleRef.RESEARCHER.value)]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # ``IUserResponse`` aliases ``user_id`` to ``userId`` on serialisation.
    new_user_id = UUID(body.get("userId") or body["user_id"])

    cognito = boto3.client("cognito-idp")
    pool_users = cognito.list_users(UserPoolId=cognito_user_pool["pool_id"])["Users"]
    pool_subs = {
        next(a["Value"] for a in u["Attributes"] if a["Name"] == "sub")
        for u in pool_users
    }
    assert str(new_user_id) in pool_subs

    audit = session.exec(
        select(UsersAudit).where(UsersAudit.user_id == new_user_id).where(UsersAudit.action == "Registered user")
    ).first()
    assert audit is not None
    assert audit.modified_by_user_id == admin_id


def test_register_user_duplicate_email_returns_400(
    client: TestClient, session, cognito_user_pool
):
    """Cognito's ``UsernameExistsException`` must surface as 400, not 500."""
    admin_id = admin_user(session)
    override_verify_token_as(admin_id)
    _admin_create_user(cognito_user_pool["pool_id"], "dupe@example.com")

    response = client.post(
        "/api/users/",
        json={"email": "dupe@example.com", "roles": [str(RoleRef.RESEARCHER.value)]},
    )

    assert response.status_code == 400, response.text
    assert "already exists" in response.text.lower()


def test_register_user_403_for_non_admin(client: TestClient, session, cognito_user_pool):
    """Researcher cannot register users — CAN_MANAGE_USERS is admin-only."""
    user_id = uuid4()
    session.add(UserRole(user_id=user_id, role_id=RoleRef.RESEARCHER.value))
    session.commit()
    override_verify_token_as(user_id)

    response = client.post(
        "/api/users/",
        json={"email": "another@example.com", "roles": [str(RoleRef.RESEARCHER.value)]},
    )

    assert response.status_code == 403, response.text


# ---------------------------------------------------------------------------
# DELETE /api/users/{user_id}  (delete_user)
# ---------------------------------------------------------------------------


def test_delete_user_removes_user_from_pool_and_drops_role_grants(
    client: TestClient, session, cognito_user_pool
):
    """Endpoint must remove the user from Cognito, drop user_role rows, and write audit."""
    admin_id = admin_user(session)
    override_verify_token_as(admin_id)
    sub = _admin_create_user(cognito_user_pool["pool_id"], "delete.me@example.com")
    # Pre-seed a role grant so we can assert it's dropped on delete.
    session.add(UserRole(user_id=UUID(sub), role_id=RoleRef.RESEARCHER.value))
    session.commit()

    response = client.delete(f"/api/users/{sub}")
    assert response.status_code == 200, response.text

    cognito = boto3.client("cognito-idp")
    remaining = cognito.list_users(UserPoolId=cognito_user_pool["pool_id"])["Users"]
    remaining_subs = {
        next(a["Value"] for a in u["Attributes"] if a["Name"] == "sub")
        for u in remaining
    }
    assert sub not in remaining_subs

    leftover_roles = session.exec(select(UserRole).where(UserRole.user_id == UUID(sub))).all()
    assert leftover_roles == []

    audit = session.exec(
        select(UsersAudit).where(UsersAudit.user_id == UUID(sub)).where(UsersAudit.action == "Deleted user")
    ).first()
    assert audit is not None
    assert audit.modified_by_user_id == admin_id


# ---------------------------------------------------------------------------
# PUT /api/users/{user_id}  (update_user — disable/enable flag)
# ---------------------------------------------------------------------------


def test_update_user_toggles_enabled_flag_in_pool(
    client: TestClient, session, cognito_user_pool
):
    """``disabled=True`` must call admin_disable_user; pool reflects the change."""
    admin_id = admin_user(session)
    override_verify_token_as(admin_id)
    sub = _admin_create_user(cognito_user_pool["pool_id"], "togglable@example.com")

    response = client.put(f"/api/users/{sub}", json={"disabled": True})
    assert response.status_code == 200, response.text

    cognito = boto3.client("cognito-idp")
    user = cognito.admin_get_user(UserPoolId=cognito_user_pool["pool_id"], Username="togglable@example.com")
    assert user["Enabled"] is False


def test_update_user_404_for_unknown_user(client: TestClient, session, cognito_user_pool):
    """A sub that doesn't exist in the pool must 404, not 500."""
    admin_id = admin_user(session)
    override_verify_token_as(admin_id)

    response = client.put(f"/api/users/{uuid4()}", json={"disabled": True})

    assert response.status_code == 404, response.text


def test_update_user_queues_xnat_profile_task(
    client: TestClient, session, cognito_user_pool, trust_factory
):
    """Production code queues a ``TrustTask`` per trust on every user update."""
    admin_id = admin_user(session)
    override_verify_token_as(admin_id)
    sub = _admin_create_user(cognito_user_pool["pool_id"], "queueable@example.com")
    # One trust seeded — the update path enumerates all trusts and writes one
    # task per trust to drive the XNAT profile sync.
    session.add(trust_factory())
    session.commit()

    response = client.put(f"/api/users/{sub}", json={"disabled": False})
    assert response.status_code == 200, response.text

    tasks = session.exec(select(TrustTask)).all()
    assert any(task.task_type == TaskType.UPDATE_USER_PROFILE for task in tasks), (
        f"expected an UPDATE_USER_PROFILE task; saw: {[t.task_type for t in tasks]}"
    )


# ---------------------------------------------------------------------------
# GET /api/users  (get_users)
# ---------------------------------------------------------------------------


def test_get_users_returns_pool_members(client: TestClient, session, cognito_user_pool):
    """``GET /api/users`` reads from Cognito; identity is sourced from the pool, not the DB."""
    admin_id = admin_user(session)
    override_verify_token_as(admin_id)

    pool_id = cognito_user_pool["pool_id"]
    seeded_emails = ["alice@example.com", "bob@example.com", "carol@example.com"]
    for email in seeded_emails:
        _admin_create_user(pool_id, email)

    response = client.get("/api/users")
    assert response.status_code == 200, response.text
    body = response.json()

    # ``GET /api/users`` returns a paginated envelope with the users in ``data``.
    emails_returned = {u["email"] for u in body["data"]}
    for email in seeded_emails:
        assert email in emails_returned, f"missing {email} in {emails_returned}"
