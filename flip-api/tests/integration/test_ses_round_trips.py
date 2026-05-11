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

"""Email-path integration tests for ``access_request`` and ``imaging_notifications``.

The production code uses ``boto3.client("sesv2", ...)`` and templated
``Content.Template`` payloads for every outbound email. moto v5 implements
the rest of the sesv2 surface (``CreateEmailIdentity``, ``ListEmailIdentities``)
but explicitly raises ``NotImplementedError("Template functionality not ready")``
on ``send_email`` with templated content, so the end-to-end path can't be
fully closed against moto today.

The next-best thing — and what these tests assert — is that the production
code path executes correctly up to the SDK boundary and the boto3 call
carries the right shape. The ``ses_send_email_recorder`` fixture (in
``conftest.py``) intercepts ``sesv2.send_email`` and records every call.
The handlers inside flip-api still construct templates, decrypt
credentials, query the DB, etc. — the only thing replaced is moto's broken
template handling.
"""

import json
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlmodel import select

from flip_api.config import get_settings
from flip_api.db.models.main_models import (
    Queries,
    TaskStatus,
    TaskType,
    TrustTask,
    XNATImageStatus,
    XNATProjectStatus,
)
from flip_api.private_services.imaging_notifications import handle_imaging_task_completed
from flip_api.utils.constants import (
    ACCESS_REQUEST_TEMPLATE_NAME,
    IMAGING_CREDENTIALS_TEMPLATE_NAME,
    IMAGING_PROJECT_ACCESS_TEMPLATE_NAME,
)
from flip_api.utils.encryption import encrypt

# ---------------------------------------------------------------------------
# POST /api/users/access  (access_request)
# ---------------------------------------------------------------------------


def test_request_access_dispatches_templated_email_to_admin(
    client: TestClient, ses_send_email_recorder
):
    """Access-request endpoint sends one templated email to the admin."""
    response = client.post(
        "/api/users/access",
        json={
            "email": "applicant@example.com",
            "full_name": "Ada Applicant",
            "reason_for_access": "Researching FLIP for ICU project",
        },
    )

    assert response.status_code == 204, response.text
    assert len(ses_send_email_recorder) == 1, f"expected 1 send_email; saw {len(ses_send_email_recorder)}"

    call = ses_send_email_recorder[0]
    settings = get_settings()
    assert call["FromEmailAddress"] == settings.AWS_SES_SENDER_EMAIL_ADDRESS
    assert call["Destination"]["ToAddresses"] == [settings.AWS_SES_ADMIN_EMAIL_ADDRESS]
    template = call["Content"]["Template"]
    assert template["TemplateName"] == ACCESS_REQUEST_TEMPLATE_NAME
    template_data = json.loads(template["TemplateData"])
    assert template_data == {
        "email": "applicant@example.com",
        "name": "Ada Applicant",
        "purpose": "Researching FLIP for ICU project",
    }


def test_request_access_validates_email_shape(client: TestClient, ses_send_email_recorder):
    """Bad email payloads must 422 before any SES call is attempted."""
    response = client.post(
        "/api/users/access",
        json={
            "email": "not-an-email",
            "full_name": "Ada Applicant",
            "reason_for_access": "x",
        },
    )

    assert response.status_code == 422, response.text
    assert ses_send_email_recorder == []


# ---------------------------------------------------------------------------
# imaging_notifications.handle_imaging_task_completed
# ---------------------------------------------------------------------------


def _seed_completed_imaging_task(session, trust_id: UUID, project_id: UUID) -> TrustTask:
    """Create a CREATE_IMAGING task with a populated result block.

    Result mirrors what trust-api returns when an XNAT project is created:
    one newly created user (gets a credentials email) and one already-
    existing user being added to the project (gets a project-access
    notification).
    """
    encrypted_password = encrypt("hunter2-the-password")  # pragma: allowlist secret
    payload = {"project_id": str(project_id)}
    # The result schema is the trust-side ``ICreatedImagingProject``, which the
    # parser deserialises with ``ID`` -> ``imaging_project_id``. Both
    # ``created_users`` and ``added_users`` need a structured user shape with
    # email + (encrypted) password where applicable.
    result = {
        "ID": str(uuid4()),
        "name": "ICU-Imaging-Project",
        "created_users": [
            {
                "username": "newbie@example.com",
                "encrypted_password": encrypted_password,
                "email": "newbie@example.com",
            }
        ],
        "added_users": [
            {"username": "existing@example.com", "email": "existing@example.com"},
        ],
    }
    task = TrustTask(
        id=uuid4(),
        trust_id=trust_id,
        task_type=TaskType.CREATE_IMAGING,
        status=TaskStatus.COMPLETED,
        payload=json.dumps(payload),
        result=json.dumps(result),
    )
    session.add(task)
    session.commit()
    return task


def test_handle_imaging_task_sends_one_email_per_user_and_persists_status(
    session, ses_send_email_recorder, trust_factory, project_factory
):
    """Status row gets persisted; one email per user (created + added)."""
    trust = trust_factory()
    project = project_factory()
    session.add(trust)
    session.add(project)
    session.commit()
    # Latest query for the project — required by the persistence helper.
    session.add(Queries(id=uuid4(), name="b2-q", query="select 1", project_id=project.id))
    session.commit()
    task = _seed_completed_imaging_task(session, trust.id, project.id)

    handle_imaging_task_completed(task, session)

    template_names = [c["Content"]["Template"]["TemplateName"] for c in ses_send_email_recorder]
    assert template_names.count(IMAGING_CREDENTIALS_TEMPLATE_NAME) == 1
    assert template_names.count(IMAGING_PROJECT_ACCESS_TEMPLATE_NAME) == 1

    # Status row: persisted exactly once.
    statuses = session.exec(
        select(XNATProjectStatus).where(
            XNATProjectStatus.trust_id == trust.id,
            XNATProjectStatus.project_id == project.id,
        )
    ).all()
    assert len(statuses) == 1
    assert statuses[0].retrieve_image_status == XNATImageStatus.CREATED


def test_handle_imaging_task_idempotent_on_status_row(
    session, ses_send_email_recorder, trust_factory, project_factory
):
    """Re-running for the same trust+project must not double-insert the status row.

    Locks in the explicit "skip if already exists from a prior attempt"
    branch in ``handle_imaging_task_completed`` — if that branch ever
    silently regresses, the duplicate row is a real DB problem.
    """
    trust = trust_factory()
    project = project_factory()
    session.add(trust)
    session.add(project)
    session.commit()
    session.add(Queries(id=uuid4(), name="b2-q", query="select 1", project_id=project.id))
    session.commit()
    task = _seed_completed_imaging_task(session, trust.id, project.id)

    handle_imaging_task_completed(task, session)
    handle_imaging_task_completed(task, session)

    statuses = session.exec(
        select(XNATProjectStatus).where(
            XNATProjectStatus.trust_id == trust.id,
            XNATProjectStatus.project_id == project.id,
        )
    ).all()
    assert len(statuses) == 1


def test_handle_imaging_task_no_users_skips_email_path(
    session, ses_send_email_recorder, trust_factory, project_factory
):
    """A completed task with no created/added users should not call SES."""
    trust = trust_factory()
    project = project_factory()
    session.add(trust)
    session.add(project)
    session.commit()
    session.add(Queries(id=uuid4(), name="b2-q", query="select 1", project_id=project.id))
    session.commit()
    task = TrustTask(
        id=uuid4(),
        trust_id=trust.id,
        task_type=TaskType.CREATE_IMAGING,
        status=TaskStatus.COMPLETED,
        payload=json.dumps({"project_id": str(project.id)}),
        result=json.dumps(
            {
                "ID": str(uuid4()),
                "name": "Empty-Project",
                "created_users": [],
                "added_users": [],
            }
        ),
    )
    session.add(task)
    session.commit()

    handle_imaging_task_completed(task, session)

    assert ses_send_email_recorder == []
