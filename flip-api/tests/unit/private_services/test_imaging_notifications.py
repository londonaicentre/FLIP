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
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError, NoCredentialsError, NoRegionError

from flip_api.private_services.imaging_notifications import handle_imaging_task_completed
from flip_api.utils.email_sender import EmailDispatchError
from flip_api.utils.encryption import XNAT_SETUP_PATH_CONTEXT

TRUST_ID = uuid4()
PROJECT_ID = str(uuid4())
IMAGING_PROJECT_ID = str(uuid4())


def _make_task(created_users, added_users=None, project_id=PROJECT_ID):
    """Create a mock TrustTask with imaging result data."""
    task = MagicMock()
    task.trust_id = TRUST_ID
    task.payload = json.dumps({"project_id": project_id})
    task.result = json.dumps(
        {
            "ID": IMAGING_PROJECT_ID,
            "name": "Test Imaging Project",
            "created_users": created_users,
            "added_users": added_users or [],
        }
    )
    return task


TWO_USERS = [
    {"username": "user1", "encrypted_setup_path": "enc1", "email": "user1@test.com"},
    {"username": "user2", "encrypted_setup_path": "enc2", "email": "user2@test.com"},
]


def _db(*firsts):
    """A mock session whose successive ``exec()`` calls return results whose ``.first()`` yields ``firsts``."""
    db = MagicMock()
    db.exec.side_effect = [MagicMock(first=MagicMock(return_value=v)) for v in firsts]
    return db


def _mock_trust():
    trust = MagicMock()
    trust.name = "Trust_1"
    return trust


def _mock_query():
    query = MagicMock()
    query.id = uuid4()
    return query


@pytest.fixture
def mock_send_email():
    """Patch the email seam: backend selection (SES vs console) is email_sender's concern."""
    with patch("flip_api.private_services.imaging_notifications.send_templated_email") as mock:
        yield mock


@pytest.fixture
def mock_decrypt():
    with patch("flip_api.private_services.imaging_notifications.decrypt") as mock:
        mock.side_effect = lambda x, **kwargs: f"decrypted_{x}"
        yield mock


@pytest.fixture
def mock_insert_status():
    with patch("flip_api.private_services.imaging_notifications.insert_status") as mock:
        yield mock


def test_sends_email_to_each_created_user(mock_send_email, mock_decrypt, mock_insert_status):
    """Should send one SES email per created user with correct template data."""
    task = _make_task(TWO_USERS)

    mock_db = _db(_mock_query(), _mock_trust())  # query lookup, then trust lookup

    handle_imaging_task_completed(task, mock_db)

    assert mock_send_email.call_count == 2

    # Verify first user's email
    first_call = mock_send_email.call_args_list[0]
    assert first_call.kwargs["recipient"] == "user1@test.com"
    template_data = first_call.kwargs["template_data"]
    assert template_data["trust_name"] == "Trust_1"
    assert template_data["project_name"] == "Test Imaging Project"
    assert template_data["username"] == "user1"
    assert template_data["setup_path"] == "decrypted_enc1"

    # Verify second user's email
    second_call = mock_send_email.call_args_list[1]
    assert second_call.kwargs["recipient"] == "user2@test.com"


def test_inserts_xnat_project_status(mock_send_email, mock_decrypt, mock_insert_status):
    """Should call insert_status with CREATED status, correct trust/project/query IDs."""
    from flip_api.db.models.main_models import XNATImageStatus

    users = [
        {"username": "user1", "encrypted_setup_path": "enc1", "email": "user1@test.com"},
    ]
    task = _make_task(users)

    mock_query = _mock_query()
    # No existing status row, then the query lookup, then the trust.
    mock_db = _db(None, mock_query, _mock_trust())

    handle_imaging_task_completed(task, mock_db)

    mock_insert_status.assert_called_once()
    call_kwargs = mock_insert_status.call_args.kwargs
    assert call_kwargs["trust_id"] == TRUST_ID
    assert str(call_kwargs["xnat_project_id"]) == IMAGING_PROJECT_ID
    assert str(call_kwargs["project_id"]) == PROJECT_ID
    assert call_kwargs["status"] == XNATImageStatus.CREATED
    assert call_kwargs["query_id"] == mock_query.id
    assert call_kwargs["db"] is mock_db


def test_inserts_status_with_no_query(mock_send_email, mock_decrypt, mock_insert_status):
    """Should pass query_id=None when project has no queries."""
    users = [
        {"username": "user1", "encrypted_setup_path": "enc1", "email": "user1@test.com"},
    ]
    task = _make_task(users)

    # No existing status row, then the query lookup (no query exists), then the trust.
    mock_db = _db(None, None, _mock_trust())

    handle_imaging_task_completed(task, mock_db)

    mock_insert_status.assert_called_once()
    assert mock_insert_status.call_args.kwargs["query_id"] is None


def test_no_emails_when_no_users_at_all(mock_send_email, mock_decrypt, mock_insert_status):
    """Should skip email sending when no created or added users, but still insert status."""
    task = _make_task(created_users=[], added_users=[])
    mock_db = _db(None, _mock_query())  # no existing status row, then the query lookup

    handle_imaging_task_completed(task, mock_db)

    mock_insert_status.assert_called_once()
    mock_send_email.assert_not_called()


def test_single_recipient_rejection_does_not_raise(mock_send_email, mock_decrypt, mock_insert_status):
    """The common batch is one created user and no added users (FLIP#1081 review).

    A rejected address there — a typo, or ``MessageRejected`` under a sandboxed
    SES identity (#592) — must not be mistaken for an outage. Counting failures
    could not tell the two apart at this size, and because
    ``retry_failed_post_processing`` has no attempt cap and never increments
    ``retry_count``, raising would re-send every sweep indefinitely with the
    task never reaching a terminal state.
    """
    task = _make_task(
        [
            {"username": "user1", "encrypted_setup_path": "enc1", "email": "typo@test.com"},
        ]
    )

    mock_db = _db(_mock_query(), _mock_trust())

    mock_send_email.side_effect = ClientError(
        {"Error": {"Code": "MessageRejected", "Message": "Email address is not verified"}}, "SendEmail"
    )

    handle_imaging_task_completed(task, mock_db)

    assert mock_send_email.call_count == 1


@pytest.mark.parametrize(
    ("created", "added", "error"),
    [
        (TWO_USERS, [{"username": "existing1", "email": "existing1@test.com"}], NoRegionError),
        ([{"username": "user1", "encrypted_setup_path": "enc1", "email": "user1@test.com"}], [], NoCredentialsError),
        ([], [{"username": "existing1", "email": "existing1@test.com"}], NoRegionError),
    ],
    ids=["batch-aborts-on-first", "single-created-recipient", "added-user-loop"],
)
def test_systemic_failure_raises_so_the_task_stays_retryable(
    created, added, error, mock_send_email, mock_decrypt, mock_insert_status
):
    """A systemic failure must NOT return cleanly — that would discard the retry.

    Both callers clear ``needs_post_processing`` only on a clean return
    (``trust_tasks``, and the ``stale_task_recovery`` sweep), so swallowing a
    systemic failure — a broken AWS region, missing credentials, an SES outage —
    would permanently drop the notifications with nothing left in the retry
    queue. It aborts on the first recipient rather than working through the rest:
    a client that cannot be constructed cannot send to anyone.

    ``single-created-recipient`` is the counterpart of
    test_single_recipient_rejection_does_not_raise: the same one-recipient shape,
    so the outcome is decided by the exception type rather than the count — keep
    it a one-recipient case. ``added-user-loop`` pins that the added-user loop
    classifies identically to the created-user loop.
    """
    task = _make_task(created, added_users=added)
    mock_db = _db(_mock_query(), _mock_trust())

    mock_send_email.side_effect = error()

    with pytest.raises(EmailDispatchError, match="failed systemically"):
        handle_imaging_task_completed(task, mock_db)

    assert mock_send_email.call_count == 1


def test_partial_failure_does_not_raise(mock_send_email, mock_decrypt, mock_insert_status):
    """One bad address is not systemic: the run succeeds so the task is not retried forever."""
    task = _make_task(TWO_USERS)

    mock_db = _db(_mock_query(), _mock_trust())

    mock_send_email.side_effect = [Exception("bad address"), None]

    handle_imaging_task_completed(task, mock_db)

    assert mock_send_email.call_count == 2


def test_decrypts_setup_paths_for_the_xnat_setup_path_context(mock_send_email, mock_decrypt, mock_insert_status):
    """The invite link is sealed by imaging-api under the ``xnat_setup_path`` context and opened under the same."""
    users = [{"username": "user1", "encrypted_setup_path": "enc1", "email": "user1@test.com"}]
    task = _make_task(users)
    mock_db = MagicMock()
    query_result = MagicMock()
    query_result.first.return_value = _mock_query()
    trust_result = MagicMock()
    trust_result.first.return_value = _mock_trust()
    mock_db.exec.side_effect = [query_result, trust_result]

    handle_imaging_task_completed(task, mock_db)

    mock_decrypt.assert_called_once_with("enc1", context=XNAT_SETUP_PATH_CONTEXT)


def test_decryption_failure_raises_so_the_task_stays_retryable(mock_send_email, mock_insert_status):
    """An invite link that fails authentication is a key or version mismatch with the trust, not a bad recipient.

    Sending nothing and completing the task would lose the only copy of the set-password link; raising leaves
    the task for post-processing so it is retried once the keys agree.
    """
    from cryptography.exceptions import InvalidTag

    users = [
        {"username": "user1", "encrypted_setup_path": "enc1", "email": "user1@test.com"},
        {"username": "user2", "encrypted_setup_path": "enc2", "email": "user2@test.com"},
    ]
    task = _make_task(users)
    mock_db = MagicMock()
    query_result = MagicMock()
    query_result.first.return_value = _mock_query()
    trust_result = MagicMock()
    trust_result.first.return_value = _mock_trust()
    mock_db.exec.side_effect = [query_result, trust_result]

    with patch("flip_api.private_services.imaging_notifications.decrypt") as mock_decrypt:
        mock_decrypt.side_effect = [InvalidTag(), "plain2"]

        with pytest.raises(EmailDispatchError, match="failed authentication"):
            handle_imaging_task_completed(task, mock_db)

    mock_send_email.assert_not_called()


@pytest.mark.parametrize(
    ("result", "error", "match"),
    [
        (None, ValueError, "no result data"),
        ("not valid json{{", json.JSONDecodeError, None),
        (json.dumps({"created_users": []}), ValueError, "missing required fields"),  # Missing ID and name
        (json.dumps({"name": "Test", "created_users": []}), ValueError, "ID"),  # Missing ID
    ],
    ids=["no-result", "malformed-json", "missing-required-fields", "missing-id"],
)
def test_invalid_result_raises(result, error, match):
    """Should raise when the task result is missing, not JSON, or lacks the required ID or name fields."""
    task = MagicMock(result=result, id="task-123")

    with pytest.raises(error, match=match):
        handle_imaging_task_completed(task, MagicMock())


def test_sends_project_access_email_to_added_users(mock_send_email, mock_decrypt, mock_insert_status):
    """Should send project access emails (no invite link) to existing users added to the project."""
    added_users = [
        {"username": "existing1", "email": "existing1@test.com"},
        {"username": "existing2", "email": "existing2@test.com"},
    ]
    task = _make_task(created_users=[], added_users=added_users)

    mock_db = _db(_mock_query(), _mock_trust())

    handle_imaging_task_completed(task, mock_db)

    assert mock_send_email.call_count == 2

    # Verify correct template is used (not the invite template)
    first_call = mock_send_email.call_args_list[0]
    assert first_call.kwargs["recipient"] == "existing1@test.com"
    assert first_call.kwargs["template_name"] == "flip-xnat-added-to-project"

    template_data = first_call.kwargs["template_data"]
    assert template_data["trust_name"] == "Trust_1"
    assert template_data["project_name"] == "Test Imaging Project"
    assert template_data["username"] == "existing1"
    assert "setup_path" not in template_data


def test_sends_both_invite_and_access_emails(mock_send_email, mock_decrypt, mock_insert_status):
    """Should send invite emails to created users AND access emails to added users."""
    created_users = [
        {"username": "new1", "encrypted_setup_path": "enc1", "email": "new1@test.com"},
    ]
    added_users = [
        {"username": "existing1", "email": "existing1@test.com"},
    ]
    task = _make_task(created_users=created_users, added_users=added_users)

    mock_db = _db(_mock_query(), _mock_trust())

    handle_imaging_task_completed(task, mock_db)

    assert mock_send_email.call_count == 2

    # First call: invite email to new user
    cred_call = mock_send_email.call_args_list[0]
    assert cred_call.kwargs["template_name"] == "flip-xnat-invite"
    assert cred_call.kwargs["recipient"] == "new1@test.com"

    # Second call: access email to existing user
    access_call = mock_send_email.call_args_list[1]
    assert access_call.kwargs["template_name"] == "flip-xnat-added-to-project"
    assert access_call.kwargs["recipient"] == "existing1@test.com"


def test_added_user_email_failure_continues(mock_send_email, mock_decrypt, mock_insert_status):
    """Should continue sending to remaining added users if SES fails for one."""
    added_users = [
        {"username": "existing1", "email": "existing1@test.com"},
        {"username": "existing2", "email": "existing2@test.com"},
    ]
    task = _make_task(created_users=[], added_users=added_users)

    mock_db = _db(_mock_query(), _mock_trust())

    mock_send_email.side_effect = [Exception("SES error"), None]

    handle_imaging_task_completed(task, mock_db)

    assert mock_send_email.call_count == 2
