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

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from flip_api.private_services.imaging_notifications import handle_imaging_task_completed

TRUST_ID = uuid4()
PROJECT_ID = str(uuid4())
IMAGING_PROJECT_ID = str(uuid4())


def _make_task(created_users, added_users=None, project_id=PROJECT_ID):
    """Create a mock TrustTask with imaging result data."""
    task = MagicMock()
    task.trust_id = TRUST_ID
    task.payload = json.dumps({"project_id": project_id})
    task.result = json.dumps({
        "ID": IMAGING_PROJECT_ID,
        "name": "Test Imaging Project",
        "created_users": created_users,
        "added_users": added_users or [],
    })
    return task


def _mock_trust():
    trust = MagicMock()
    trust.name = "Trust_1"
    return trust


def _mock_query():
    query = MagicMock()
    query.id = uuid4()
    return query


@pytest.fixture
def mock_ses():
    with patch("flip_api.private_services.imaging_notifications.boto3") as mock_boto3:
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        yield mock_client


@pytest.fixture
def mock_decrypt():
    with patch("flip_api.private_services.imaging_notifications.decrypt") as mock:
        mock.side_effect = lambda x: f"decrypted_{x}"
        yield mock


@pytest.fixture
def mock_settings():
    with patch("flip_api.private_services.imaging_notifications.get_settings") as mock:
        mock.return_value = SimpleNamespace(
            AWS_REGION="eu-west-2",
            AWS_SES_SENDER_EMAIL_ADDRESS="noreply@test.com",
        )
        yield mock


@pytest.fixture
def mock_insert_status():
    with patch("flip_api.private_services.imaging_notifications.insert_status") as mock:
        yield mock


def test_sends_email_to_each_created_user(mock_ses, mock_decrypt, mock_settings, mock_insert_status):
    """Should send one SES email per created user with correct template data."""
    users = [
        {"username": "user1", "encrypted_password": "enc1", "email": "user1@test.com"},
        {"username": "user2", "encrypted_password": "enc2", "email": "user2@test.com"},
    ]
    task = _make_task(users)

    mock_db = MagicMock()
    # First exec: query lookup; second: trust lookup
    query_result = MagicMock()
    query_result.first.return_value = _mock_query()
    trust_result = MagicMock()
    trust_result.first.return_value = _mock_trust()
    mock_db.exec.side_effect = [query_result, trust_result]

    handle_imaging_task_completed(task, mock_db)

    assert mock_ses.send_email.call_count == 2

    # Verify first user's email
    first_call = mock_ses.send_email.call_args_list[0]
    assert first_call.kwargs["Destination"] == {"ToAddresses": ["user1@test.com"]}
    template_data = json.loads(first_call.kwargs["Content"]["Template"]["TemplateData"])
    assert template_data["trust_name"] == "Trust_1"
    assert template_data["project_name"] == "Test Imaging Project"
    assert template_data["username"] == "user1"
    assert template_data["password"] == "decrypted_enc1"

    # Verify second user's email
    second_call = mock_ses.send_email.call_args_list[1]
    assert second_call.kwargs["Destination"] == {"ToAddresses": ["user2@test.com"]}


def test_inserts_xnat_project_status(mock_ses, mock_decrypt, mock_settings, mock_insert_status):
    """Should call insert_status with CREATED status, correct trust/project/query IDs."""
    from flip_api.db.models.main_models import XNATImageStatus

    users = [
        {"username": "user1", "encrypted_password": "enc1", "email": "user1@test.com"},
    ]
    task = _make_task(users)

    mock_query = _mock_query()
    mock_db = MagicMock()
    idempotency_result = MagicMock()
    idempotency_result.first.return_value = None  # No existing status row
    query_result = MagicMock()
    query_result.first.return_value = mock_query
    trust_result = MagicMock()
    trust_result.first.return_value = _mock_trust()
    mock_db.exec.side_effect = [idempotency_result, query_result, trust_result]

    handle_imaging_task_completed(task, mock_db)

    mock_insert_status.assert_called_once()
    call_kwargs = mock_insert_status.call_args.kwargs
    assert call_kwargs["trust_id"] == TRUST_ID
    assert str(call_kwargs["xnat_project_id"]) == IMAGING_PROJECT_ID
    assert str(call_kwargs["project_id"]) == PROJECT_ID
    assert call_kwargs["status"] == XNATImageStatus.CREATED
    assert call_kwargs["query_id"] == mock_query.id
    assert call_kwargs["db"] is mock_db


def test_inserts_status_with_no_query(mock_ses, mock_decrypt, mock_settings, mock_insert_status):
    """Should pass query_id=None when project has no queries."""
    users = [
        {"username": "user1", "encrypted_password": "enc1", "email": "user1@test.com"},
    ]
    task = _make_task(users)

    mock_db = MagicMock()
    idempotency_result = MagicMock()
    idempotency_result.first.return_value = None  # No existing status row
    query_result = MagicMock()
    query_result.first.return_value = None  # No query exists
    trust_result = MagicMock()
    trust_result.first.return_value = _mock_trust()
    mock_db.exec.side_effect = [idempotency_result, query_result, trust_result]

    handle_imaging_task_completed(task, mock_db)

    mock_insert_status.assert_called_once()
    assert mock_insert_status.call_args.kwargs["query_id"] is None


def test_no_emails_when_no_users_at_all(mock_ses, mock_decrypt, mock_settings, mock_insert_status):
    """Should skip email sending when no created or added users, but still insert status."""
    task = _make_task(created_users=[], added_users=[])
    mock_db = MagicMock()
    idempotency_result = MagicMock()
    idempotency_result.first.return_value = None  # No existing status row
    query_result = MagicMock()
    query_result.first.return_value = _mock_query()
    mock_db.exec.side_effect = [idempotency_result, query_result]

    handle_imaging_task_completed(task, mock_db)

    mock_insert_status.assert_called_once()
    mock_ses.send_email.assert_not_called()


def test_ses_failure_for_one_user_continues_to_next(mock_ses, mock_decrypt, mock_settings, mock_insert_status):
    """Should continue sending to remaining users if SES fails for one."""
    users = [
        {"username": "user1", "encrypted_password": "enc1", "email": "user1@test.com"},
        {"username": "user2", "encrypted_password": "enc2", "email": "user2@test.com"},
    ]
    task = _make_task(users)

    mock_db = MagicMock()
    query_result = MagicMock()
    query_result.first.return_value = _mock_query()
    trust_result = MagicMock()
    trust_result.first.return_value = _mock_trust()
    mock_db.exec.side_effect = [query_result, trust_result]

    mock_ses.send_email.side_effect = [Exception("SES error"), None]

    handle_imaging_task_completed(task, mock_db)

    assert mock_ses.send_email.call_count == 2


def test_decryption_failure_continues_to_next_user(mock_ses, mock_settings, mock_insert_status):
    """Should continue to next user if decryption fails for one."""
    users = [
        {"username": "user1", "encrypted_password": "enc1", "email": "user1@test.com"},
        {"username": "user2", "encrypted_password": "enc2", "email": "user2@test.com"},
    ]
    task = _make_task(users)

    mock_db = MagicMock()
    query_result = MagicMock()
    query_result.first.return_value = _mock_query()
    trust_result = MagicMock()
    trust_result.first.return_value = _mock_trust()
    mock_db.exec.side_effect = [query_result, trust_result]

    with patch("flip_api.private_services.imaging_notifications.decrypt") as mock_decrypt:
        mock_decrypt.side_effect = [Exception("Decryption failed"), "plain2"]

        handle_imaging_task_completed(task, mock_db)

        # Only second user should get an email
        assert mock_ses.send_email.call_count == 1
        call_args = mock_ses.send_email.call_args
        assert call_args.kwargs["Destination"] == {"ToAddresses": ["user2@test.com"]}


def test_raises_when_result_is_none():
    """Should raise ValueError when task result is None."""
    task = MagicMock()
    task.result = None
    task.id = "task-123"
    mock_db = MagicMock()

    with pytest.raises(ValueError, match="no result data"):
        handle_imaging_task_completed(task, mock_db)


def test_malformed_result_json_raises():
    """Should raise when task result contains invalid JSON."""
    task = MagicMock()
    task.result = "not valid json{{"
    mock_db = MagicMock()

    with pytest.raises(json.JSONDecodeError):
        handle_imaging_task_completed(task, mock_db)


def test_missing_required_fields_raises_value_error():
    """Should raise ValueError when result is missing required ID or name fields."""
    task = MagicMock()
    task.result = json.dumps({"created_users": []})  # Missing ID and name
    mock_db = MagicMock()

    with pytest.raises(ValueError, match="missing required fields"):
        handle_imaging_task_completed(task, mock_db)


def test_missing_id_field_raises_value_error():
    """Should raise ValueError when result is missing the ID field."""
    task = MagicMock()
    task.result = json.dumps({"name": "Test", "created_users": []})  # Missing ID
    mock_db = MagicMock()

    with pytest.raises(ValueError, match="ID"):
        handle_imaging_task_completed(task, mock_db)


def test_sends_project_access_email_to_added_users(mock_ses, mock_decrypt, mock_settings, mock_insert_status):
    """Should send project access emails (no password) to existing users added to the project."""
    added_users = [
        {"username": "existing1", "email": "existing1@test.com"},
        {"username": "existing2", "email": "existing2@test.com"},
    ]
    task = _make_task(created_users=[], added_users=added_users)

    mock_db = MagicMock()
    query_result = MagicMock()
    query_result.first.return_value = _mock_query()
    trust_result = MagicMock()
    trust_result.first.return_value = _mock_trust()
    mock_db.exec.side_effect = [query_result, trust_result]

    handle_imaging_task_completed(task, mock_db)

    assert mock_ses.send_email.call_count == 2

    # Verify correct template is used (not credentials template)
    first_call = mock_ses.send_email.call_args_list[0]
    assert first_call.kwargs["Destination"] == {"ToAddresses": ["existing1@test.com"]}
    assert first_call.kwargs["Content"]["Template"]["TemplateName"] == "flip-xnat-added-to-project"

    template_data = json.loads(first_call.kwargs["Content"]["Template"]["TemplateData"])
    assert template_data["trust_name"] == "Trust_1"
    assert template_data["project_name"] == "Test Imaging Project"
    assert template_data["username"] == "existing1"
    assert "password" not in template_data


def test_sends_both_credential_and_access_emails(mock_ses, mock_decrypt, mock_settings, mock_insert_status):
    """Should send credential emails to created users AND access emails to added users."""
    created_users = [
        {"username": "new1", "encrypted_password": "enc1", "email": "new1@test.com"},
    ]
    added_users = [
        {"username": "existing1", "email": "existing1@test.com"},
    ]
    task = _make_task(created_users=created_users, added_users=added_users)

    mock_db = MagicMock()
    query_result = MagicMock()
    query_result.first.return_value = _mock_query()
    trust_result = MagicMock()
    trust_result.first.return_value = _mock_trust()
    mock_db.exec.side_effect = [query_result, trust_result]

    handle_imaging_task_completed(task, mock_db)

    assert mock_ses.send_email.call_count == 2

    # First call: credentials email to new user
    cred_call = mock_ses.send_email.call_args_list[0]
    assert cred_call.kwargs["Content"]["Template"]["TemplateName"] == "flip-xnat-credentials"
    assert cred_call.kwargs["Destination"] == {"ToAddresses": ["new1@test.com"]}

    # Second call: access email to existing user
    access_call = mock_ses.send_email.call_args_list[1]
    assert access_call.kwargs["Content"]["Template"]["TemplateName"] == "flip-xnat-added-to-project"
    assert access_call.kwargs["Destination"] == {"ToAddresses": ["existing1@test.com"]}


def test_added_user_email_failure_continues(mock_ses, mock_decrypt, mock_settings, mock_insert_status):
    """Should continue sending to remaining added users if SES fails for one."""
    added_users = [
        {"username": "existing1", "email": "existing1@test.com"},
        {"username": "existing2", "email": "existing2@test.com"},
    ]
    task = _make_task(created_users=[], added_users=added_users)

    mock_db = MagicMock()
    query_result = MagicMock()
    query_result.first.return_value = _mock_query()
    trust_result = MagicMock()
    trust_result.first.return_value = _mock_trust()
    mock_db.exec.side_effect = [query_result, trust_result]

    mock_ses.send_email.side_effect = [Exception("SES error"), None]

    handle_imaging_task_completed(task, mock_db)

    assert mock_ses.send_email.call_count == 2
