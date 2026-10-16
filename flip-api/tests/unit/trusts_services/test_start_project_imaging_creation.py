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
import uuid
from unittest import mock
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from flip_api.domain.interfaces.project import IProjectQuery, IProjectResponse
from flip_api.domain.interfaces.trust import ITrust
from flip_api.domain.schemas.status import DecisionMaker, ProjectStatus, TaskType
from flip_api.domain.schemas.users import CognitoUser
from flip_api.trusts_services.start_project_imaging_creation import (
    queue_cohort_snapshot,
    queue_imaging_creation,
    start_project_imaging_creation,
)

# =============================================================================================
# Test data
# =============================================================================================
project_id = uuid.uuid4()
trust_id = uuid.uuid4()
trust_example = ITrust(id=trust_id, name="Example Trust")

# User data
user_id = uuid.uuid4()
user_name = "user one"
user_email = "user1@example.com"
user_encrypted_setup_path = "encrypted_setup_path"
# =============================================================================================


# Helper mock functions
@pytest.fixture
def mock_request():
    request = MagicMock()
    request.state.user.sub = user_id
    return request


@pytest.fixture
def mock_get_session():
    with mock.patch("flip_api.trusts_services.start_project_imaging_creation.get_session") as mock_get_session:
        mock_get_session.return_value = MagicMock()
        yield mock_get_session


@pytest.fixture
def mock_has_permissions():
    """The route authorises the trust named in the body by the approval endpoint's per-trust rule (FLIP#1258).

    Fixture kept as ``has_permissions`` for the existing call sites; the seam under it is
    ``decision_maker_for``, patched to allow (HUB) unless a test sets it to None.
    """
    with mock.patch(
        "flip_api.trusts_services.start_project_imaging_creation.decision_maker_for"
    ) as mock_has_permissions:
        mock_has_permissions.return_value = DecisionMaker.HUB
        yield mock_has_permissions


@pytest.fixture
def mock_get_project():
    with mock.patch("flip_api.trusts_services.start_project_imaging_creation.get_project") as mock_get_project:
        query_id = uuid.uuid4()
        query = IProjectQuery(
            id=query_id,
            name="Test Query",
            query="SELECT * FROM table",
            queried_trust_ids=[uuid.uuid4(), uuid.uuid4()],
            total_cohort=20,
        )
        mock_get_project.return_value = IProjectResponse(
            id=project_id,
            name="Test Project",
            query=query,
            owner_id=user_id,
            dicom_to_nifti=True,
            status=ProjectStatus.APPROVED,
        )
        yield mock_get_project


@pytest.fixture(autouse=True)
def mock_get_approved_trusts():
    """The trust has approved the project, as imaging requires (FLIP#1258); tests refusing it clear the list."""
    with mock.patch(
        "flip_api.trusts_services.start_project_imaging_creation.get_approved_trusts_for_project"
    ) as mock_get_approved_trusts:
        mock_get_approved_trusts.return_value = [trust_example]
        yield mock_get_approved_trusts


@pytest.fixture
def mock_get_users_with_access():
    with mock.patch(
        "flip_api.trusts_services.start_project_imaging_creation.get_users_with_access"
    ) as mock_get_users_with_access:
        mock_get_users_with_access.return_value = [user_email]
        yield mock_get_users_with_access


@pytest.fixture
def mock_list_users(fake_idp):
    """The identity-provider double, primed with the directory the handler lists."""
    fake_idp.list_users.return_value = [
        CognitoUser(id=user_id, email=user_email, is_disabled=False),
    ]
    return fake_idp


# Test case for permission failure
@pytest.mark.asyncio
async def test_permission_failure(mock_request, mock_get_session, mock_has_permissions, fake_idp):
    # Simulate permission denial
    mock_has_permissions.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        await start_project_imaging_creation(
            request=mock_request,
            project_id=project_id,
            trust=trust_example,
            db=mock_get_session,
            user_id=user_id,
            idp=fake_idp,
        )

    assert exc_info.value.status_code == 403
    assert f"User with ID: {user_id} was unable to start XNAT project creation" in exc_info.value.detail


# Test case for project not found
@pytest.mark.asyncio
async def test_project_not_found(mock_request, mock_get_session, mock_has_permissions, mock_get_project, fake_idp):
    # Simulate project not found
    mock_get_project.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        await start_project_imaging_creation(
            request=mock_request,
            project_id=project_id,
            trust=trust_example,
            db=mock_get_session,
            user_id=user_id,
            idp=fake_idp,
        )

    assert exc_info.value.status_code == 404
    assert (
        f"Central Hub project with {project_id=} not found. Unable to start XNAT project creation"
        in exc_info.value.detail
    )


# Test case for successful imaging project creation (now queues a task)
@pytest.mark.asyncio
async def test_successful_imaging_creation(
    mock_request,
    mock_get_session,
    mock_has_permissions,
    mock_get_project,
    mock_get_users_with_access,
    mock_list_users,
    fake_idp,
):
    response = await start_project_imaging_creation(
        request=mock_request,
        project_id=project_id,
        trust=trust_example,
        db=mock_get_session,
        user_id=user_id,
        idp=fake_idp,
    )

    assert response["success"] == "Imaging project creation task queued successfully"
    # Two tasks, in this order: the cohort snapshot (FLIP#857) is queued and committed
    # FIRST so its created_at strictly precedes the imaging task's — pending-task dispatch
    # orders by created_at and the trust poller is sequential, so the frozen accession set
    # exists by the time imaging retrieval asks for it.
    assert mock_get_session.add.call_count == 2
    assert mock_get_session.commit.call_count == 2
    persist_task = mock_get_session.add.call_args_list[0][0][0]
    imaging_task = mock_get_session.add.call_args_list[1][0][0]
    assert persist_task.task_type == TaskType.PERSIST_COHORT
    assert imaging_task.task_type == TaskType.CREATE_IMAGING
    # The task row records which Queries row was frozen, and the payload carries both the
    # encrypted id (forwarded to data-access-api) and the query of record.
    assert persist_task.query_id is not None
    persist_payload = json.loads(persist_task.payload)
    assert persist_payload["encrypted_project_id"]
    assert persist_payload["query"] == "SELECT * FROM table"

    # The directory is listed once and narrowed to the project's members for the trust payload.
    fake_idp.list_users.assert_called_once_with()
    task = mock_get_session.add.call_args[0][0]
    payload = json.loads(task.payload)
    assert [u["id"] for u in payload["users"]] == [str(user_id)]


@pytest.mark.asyncio
async def test_queue_imaging_creation_does_not_check_the_caller(
    mock_request,
    mock_get_session,
    mock_has_permissions,
    mock_get_project,
    mock_get_users_with_access,
    mock_list_users,
    fake_idp,
):
    """The approval fan-out's entry point: a trust approved by an earlier call is queued whoever completes approval."""
    response = await queue_imaging_creation(
        request=mock_request, project_id=project_id, trust=trust_example, db=mock_get_session, idp=fake_idp
    )

    assert response["success"] == "Imaging project creation task queued successfully"
    mock_has_permissions.assert_not_called()
    queued = [call.args[0].task_type for call in mock_get_session.add.call_args_list]
    assert queued == [TaskType.PERSIST_COHORT, TaskType.CREATE_IMAGING]


@pytest.mark.asyncio
async def test_a_late_trust_freezes_its_own_cohort_before_its_imaging(
    mock_request,
    mock_get_session,
    mock_get_project,
    mock_get_approved_trusts,
    mock_get_users_with_access,
    mock_list_users,
):
    """A trust approving an already-APPROVED project (FLIP#1258) joins through this same entry point.

    So it gets its own PERSIST_COHORT task (FLIP#857), committed before its CREATE_IMAGING task: the row-level
    routes serve only the frozen members, and a late trust without a frozen membership would refuse its imaging
    and training.
    """
    late_trust = ITrust(id=uuid.uuid4(), name="Late Trust")
    mock_get_approved_trusts.return_value = [trust_example, late_trust]

    await queue_imaging_creation(
        request=mock_request, project_id=project_id, trust=late_trust, db=mock_get_session, idp=mock_list_users
    )

    persist_task, imaging_task = [call.args[0] for call in mock_get_session.add.call_args_list]
    assert (persist_task.task_type, imaging_task.task_type) == (TaskType.PERSIST_COHORT, TaskType.CREATE_IMAGING)
    assert persist_task.trust_id == imaging_task.trust_id == late_trust.id
    assert json.loads(persist_task.payload)["trust_id"] == str(late_trust.id)
    # Each task in its own commit, the snapshot's first, so its created_at is the earlier.
    assert [name for name, _, _ in mock_get_session.mock_calls if name in ("add", "commit")] == [
        "add",
        "commit",
        "add",
        "commit",
    ]


# Test case for DB error during task creation
@pytest.mark.asyncio
async def test_db_error_during_task_creation(
    mock_request,
    mock_get_session,
    mock_has_permissions,
    mock_get_project,
    mock_get_users_with_access,
    mock_list_users,
    fake_idp,
):
    mock_get_session.add.side_effect = Exception("DB write failed")

    with pytest.raises(HTTPException) as exc_info:
        await start_project_imaging_creation(
            request=mock_request,
            project_id=project_id,
            trust=trust_example,
            db=mock_get_session,
            user_id=user_id,
            idp=fake_idp,
        )

    assert exc_info.value.status_code == 500
    mock_get_session.rollback.assert_called_once()
    assert "Internal server error" in exc_info.value.detail


# Test case for dicom_to_nifti=False being included in the queued task payload
@pytest.mark.asyncio
async def test_dicom_to_nifti_false_forwarded_to_trust(
    mock_request,
    mock_get_session,
    mock_has_permissions,
    mock_get_project,
    mock_get_users_with_access,
    mock_list_users,
    fake_idp,
):
    # Override fixture to set dicom_to_nifti=False
    mock_get_project.return_value.dicom_to_nifti = False

    await start_project_imaging_creation(
        request=mock_request,
        project_id=project_id,
        trust=trust_example,
        db=mock_get_session,
        user_id=user_id,
        idp=fake_idp,
    )

    # Verify the task payload includes dicom_to_nifti=False.
    # The imaging task is queued second, after the cohort-snapshot task.
    task = mock_get_session.add.call_args_list[-1][0][0]
    assert task.task_type == TaskType.CREATE_IMAGING
    payload = json.loads(task.payload)
    assert payload["dicom_to_nifti"] is False


@pytest.mark.asyncio
async def test_project_without_imaging_is_refused_with_409(
    mock_request,
    mock_get_session,
    mock_has_permissions,
    mock_get_project,
    mock_get_users_with_access,
    mock_list_users,
    fake_idp,
):
    """FLIP#1071: a direct call cannot start an imaging stage the project was created without."""
    mock_get_project.return_value.has_imaging = False

    with pytest.raises(HTTPException) as excinfo:
        await start_project_imaging_creation(
            request=mock_request,
            project_id=project_id,
            trust=trust_example,
            db=mock_get_session,
            user_id=user_id,
            idp=fake_idp,
        )

    assert excinfo.value.status_code == 409
    mock_get_session.add.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("project_status", "approved_trusts"),
    [(ProjectStatus.STAGED, [trust_example]), (ProjectStatus.APPROVED, [])],
    ids=["project not approved", "trust not approved"],
)
async def test_imaging_follows_the_trusts_approval(
    mock_request,
    mock_get_session,
    mock_get_project,
    mock_get_approved_trusts,
    fake_idp,
    project_status,
    approved_trusts,
):
    """FLIP#1258: the fan-out's entry point refuses too, so no path starts imaging a trust did not approve."""
    mock_get_project.return_value.status = project_status
    mock_get_approved_trusts.return_value = approved_trusts

    with pytest.raises(HTTPException) as excinfo:
        await queue_imaging_creation(
            request=mock_request, project_id=project_id, trust=trust_example, db=mock_get_session, idp=fake_idp
        )

    assert excinfo.value.status_code == 409
    mock_get_session.add.assert_not_called()


def test_queue_cohort_snapshot_freezes_a_project_without_imaging(mock_get_session, mock_get_project):
    """FLIP#1071 x FLIP#857: no imaging stage, but training reads the frozen cohort — so the trust freezes it."""
    mock_get_project.return_value.has_imaging = False

    response = queue_cohort_snapshot(project_id=project_id, trust=trust_example, db=mock_get_session)

    assert response["success"] == "Cohort snapshot task queued successfully"
    (persist_task,) = [call.args[0] for call in mock_get_session.add.call_args_list]
    assert (persist_task.task_type, persist_task.trust_id) == (TaskType.PERSIST_COHORT, trust_id)
    payload = json.loads(persist_task.payload)
    assert payload["encrypted_project_id"]
    assert payload["query"] == "SELECT * FROM table"
    mock_get_session.commit.assert_called_once()


@pytest.mark.parametrize(
    ("project_status", "approved_trusts"),
    [(ProjectStatus.STAGED, [trust_example]), (ProjectStatus.APPROVED, [])],
    ids=["project not approved", "trust not approved"],
)
def test_queue_cohort_snapshot_follows_the_trusts_approval(
    mock_get_session, mock_get_project, mock_get_approved_trusts, project_status, approved_trusts
):
    """As for imaging (FLIP#1258): a trust that has not approved never runs the cohort query."""
    mock_get_project.return_value.status = project_status
    mock_get_approved_trusts.return_value = approved_trusts

    with pytest.raises(HTTPException) as excinfo:
        queue_cohort_snapshot(project_id=project_id, trust=trust_example, db=mock_get_session)

    assert excinfo.value.status_code == 409
    mock_get_session.add.assert_not_called()


def test_queue_cohort_snapshot_rolls_back_a_db_error(mock_get_session, mock_get_project):
    mock_get_session.add.side_effect = Exception("DB write failed")

    with pytest.raises(HTTPException) as excinfo:
        queue_cohort_snapshot(project_id=project_id, trust=trust_example, db=mock_get_session)

    assert excinfo.value.status_code == 500
    assert excinfo.value.detail == "Internal server error"
    mock_get_session.rollback.assert_called_once()


def test_queue_cohort_snapshot_refuses_a_project_without_a_query(mock_get_session, mock_get_project):
    """No query of record means nothing to freeze: the step fails for this trust instead of reporting success."""
    mock_get_project.return_value.query = None

    with pytest.raises(HTTPException) as excinfo:
        queue_cohort_snapshot(project_id=project_id, trust=trust_example, db=mock_get_session)

    assert excinfo.value.status_code == 409
    assert "no cohort query" in excinfo.value.detail
    mock_get_session.add.assert_not_called()


@pytest.mark.asyncio
async def test_imaging_is_not_queued_for_a_project_without_a_query(
    mock_request,
    mock_get_session,
    mock_get_project,
    mock_get_users_with_access,
    mock_list_users,
):
    """The snapshot failure stops the trust's imaging too: imaging without a frozen membership would be refused."""
    mock_get_project.return_value.query = None

    with pytest.raises(HTTPException) as excinfo:
        await queue_imaging_creation(
            request=mock_request, project_id=project_id, trust=trust_example, db=mock_get_session, idp=mock_list_users
        )

    assert excinfo.value.status_code == 409
    mock_get_session.add.assert_not_called()
