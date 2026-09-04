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

"""The email paths under development's default console backend (FLIP#919).

The counterpart to ``test_ses_round_trips.py``: those tests pin the SES path
(via ``ses_send_email_recorder``, which forces ``EMAIL_BACKEND="ses"``), while
these run with the dev default untouched. They are the regression guard for
the promise that a developer can run FLIP with no SES configuration at all —
the request still succeeds, the DB is written, and the would-be email is
logged rather than sent.
"""

import json
import logging
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from flip_api.config import get_settings
from flip_api.db.models.main_models import Queries, TaskStatus, TaskType, TrustTask
from flip_api.db.models.user_models import AccessRequest
from flip_api.private_services.imaging_notifications import handle_imaging_task_completed
from flip_api.utils.constants import (
    ACCESS_REQUEST_TEMPLATE_NAME,
    IMAGING_CREDENTIALS_TEMPLATE_NAME,
    IMAGING_PROJECT_ACCESS_TEMPLATE_NAME,
)
from flip_api.utils.encryption import encrypt

XNAT_PASSWORD = "hunter2-the-password"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def console_backend(monkeypatch):
    """Pin the console backend for this module, mirroring ``ses_send_email_recorder``.

    ``DevSettings`` already defaults to ``console`` (asserted on the field
    itself in ``tests/unit/test_config.py``), but a developer may set
    ``EMAIL_BACKEND=ses`` in ``.env.development`` — the config comment
    explicitly invites that — which would otherwise turn this whole module red
    and attempt real SES calls. Pin the code path under test rather than
    depending on ambient config.
    """
    monkeypatch.setattr(get_settings(), "EMAIL_BACKEND", "console")


def test_request_access_succeeds_and_logs_instead_of_sending(client: TestClient, session, caplog):
    """The access request is persisted and flagged notified with no SES call."""
    with caplog.at_level(logging.INFO, logger="uvicorn"):
        response = client.post(
            "/api/users/access",
            json={
                "email": "applicant@example.com",
                "full_name": "Ada Applicant",
                "reason_for_access": "Researching FLIP for ICU project",
            },
        )

    assert response.status_code == 204, response.text
    assert ACCESS_REQUEST_TEMPLATE_NAME in caplog.text
    assert get_settings().AWS_SES_ADMIN_EMAIL_ADDRESS in caplog.text

    persisted = session.exec(select(AccessRequest).where(AccessRequest.email == "applicant@example.com")).one()
    # The console backend "succeeds", so the row is flagged notified — in dev
    # there is no operator working the WHERE email_notified IS false queue.
    assert persisted.email_notified is True


def test_imaging_notifications_log_both_templates_without_leaking_the_password(
    session, trust_factory, project_factory, caplog
):
    """Both XNAT emails are logged, and the decrypted password never is."""
    trust = trust_factory()
    project = project_factory()
    session.add(trust)
    session.add(project)
    session.commit()
    session.add(Queries(id=uuid4(), name="q", query="select 1", project_id=project.id, created_by=uuid4()))
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
                "name": "ICU-Imaging-Project",
                "created_users": [
                    {
                        "username": "newbie@example.com",
                        "encrypted_password": encrypt(XNAT_PASSWORD),
                        "email": "newbie@example.com",
                    }
                ],
                "added_users": [{"username": "existing@example.com", "email": "existing@example.com"}],
            }
        ),
    )
    session.add(task)
    session.commit()

    with caplog.at_level(logging.INFO, logger="uvicorn"):
        handle_imaging_task_completed(task, session)

    assert IMAGING_CREDENTIALS_TEMPLATE_NAME in caplog.text
    assert IMAGING_PROJECT_ACCESS_TEMPLATE_NAME in caplog.text
    # The credentials template carries the user's decrypted XNAT password.
    # Logging it would turn a dev convenience into a credential leak.
    assert XNAT_PASSWORD not in caplog.text
