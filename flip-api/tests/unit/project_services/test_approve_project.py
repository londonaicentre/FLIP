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

import pytest
from fastapi import HTTPException, status
from pydantic import ValidationError

from flip_api.db.models.main_models import Projects, Trust
from flip_api.domain.schemas.projects import ApproveProjectBodyPayload
from flip_api.domain.schemas.status import DecisionMaker, ProjectStatus
from flip_api.project_services.approve_project import approve_project_endpoint
from flip_api.project_services.services.project_services import (
    InvalidTrustDecisionsError,
    ProjectNotStagedError,
    TrustDecisionOutcome,
)

# Imports from the module to be tested
# Assuming sqlmodel.Session is used for type hinting or spec


# Common test data
TEST_PROJECT_ID = str(uuid.uuid4())
TEST_USER_ID = str(uuid.uuid4())
TEST_TRUST_IDS = [str(uuid.uuid4()), str(uuid.uuid4())]
DECLINED_TRUST_ID = str(uuid.uuid4())
# An approved trust that is not in the payload: approved by an earlier, partial call.
APPROVED_OUTCOME = TrustDecisionOutcome(project_status=ProjectStatus.APPROVED, activated_trust_ids=[uuid.uuid4()])
STAGED_OUTCOME = TrustDecisionOutcome(project_status=ProjectStatus.STAGED, activated_trust_ids=[])


@pytest.fixture(autouse=True)
def no_trust_has_an_admin():
    """By default every trust is hub-run; the per-trust authority itself is covered in the integration suite."""
    with patch("flip_api.project_services.approve_project.trusts_with_admin", return_value=set()) as mock:
        yield mock


@pytest.fixture
def mock_payload():
    payload = MagicMock(spec=ApproveProjectBodyPayload)
    payload.trusts = TEST_TRUST_IDS
    payload.declined = []
    return payload


@pytest.fixture
def mock_staged_project():
    project = MagicMock(spec=Projects)
    project.id = TEST_PROJECT_ID
    project.status = ProjectStatus.STAGED
    return project


@patch("flip_api.project_services.approve_project.logger")
@patch("flip_api.project_services.approve_project.get_trusts")
@patch("flip_api.project_services.approve_project.record_trust_decisions", return_value=APPROVED_OUTCOME)
@patch("flip_api.project_services.approve_project.decision_maker_for", return_value=DecisionMaker.HUB)
def test_approve_project_endpoint_success(
    mock_decision_maker_for,
    mock_record_trust_decisions,
    mock_get_trusts,  # This is the get_trusts function
    mock_logger,
    mock_db_session,  # Fixture
    mock_payload,  # Fixture
    mock_staged_project,  # Fixture
):
    """The trusts returned are the outcome's approved trusts — which can include one approved by an earlier call
    and so absent from this payload — and the payload's declined trusts reach the service."""
    # Arrange
    mock_db_session.get.return_value = mock_staged_project
    mock_payload.declined = [DECLINED_TRUST_ID]

    mock_trust_list = [MagicMock(spec=Trust), MagicMock(spec=Trust)]
    mock_get_trusts.return_value = mock_trust_list

    # Act
    result = approve_project_endpoint(
        project_id=TEST_PROJECT_ID,
        payload=mock_payload,
        user_id=TEST_USER_ID,
        db=mock_db_session,
    )

    # Assert
    # Authority is checked against EACH trust named in the payload, declined ones included, never once globally.
    assert [call.args[1] for call in mock_decision_maker_for.call_args_list] == [*TEST_TRUST_IDS, DECLINED_TRUST_ID]
    for call in mock_decision_maker_for.call_args_list:
        assert call.args[0] == TEST_USER_ID

    mock_db_session.get.assert_called_once_with(Projects, TEST_PROJECT_ID)
    decisions = mock_record_trust_decisions.call_args.args[1]
    assert decisions.trust_ids == [uuid.UUID(tid) for tid in TEST_TRUST_IDS]
    assert decisions.declined_trust_ids == [uuid.UUID(DECLINED_TRUST_ID)]
    mock_get_trusts.assert_called_once_with(mock_db_session, ids=APPROVED_OUTCOME.activated_trust_ids)

    assert result == mock_trust_list


@patch("flip_api.project_services.approve_project.get_trusts")
@patch("flip_api.project_services.approve_project.record_trust_decisions", return_value=STAGED_OUTCOME)
@patch("flip_api.project_services.approve_project.decision_maker_for", return_value=DecisionMaker.HUB)
def test_approve_project_endpoint_returns_no_trusts_while_the_project_stays_staged(
    mock_decision_maker_for,
    mock_record_trust_decisions,
    mock_get_trusts,
    mock_db_session,
    mock_payload,
    mock_staged_project,
):
    """A trust still pending, or every trust declined → no trusts back, so the caller dispatches no imaging."""
    mock_db_session.get.return_value = mock_staged_project

    result = approve_project_endpoint(
        project_id=TEST_PROJECT_ID,
        payload=mock_payload,
        user_id=TEST_USER_ID,
        db=mock_db_session,
    )

    assert result == []
    mock_get_trusts.assert_not_called()


@pytest.mark.parametrize(
    ("error", "detail"),
    [
        (
            InvalidTrustDecisionsError("Trusts ['x'] were not selected during the staging process."),
            "Trusts ['x'] were not selected during the staging process.",
        ),
        # Lost a race: another approver approved the project after the endpoint's own STAGED check.
        (
            ProjectNotStagedError("Project p is APPROVED, not STAGED."),
            "Unable to approve the project as it has not been staged",
        ),
    ],
)
@patch("flip_api.project_services.approve_project.get_trusts")
@patch("flip_api.project_services.approve_project.decision_maker_for", return_value=DecisionMaker.HUB)
def test_approve_project_endpoint_maps_refused_decisions_to_400(
    mock_decision_maker_for,
    mock_get_trusts,
    mock_db_session,
    mock_payload,
    mock_staged_project,
    error,
    detail,
):
    mock_db_session.get.return_value = mock_staged_project

    with (
        patch("flip_api.project_services.approve_project.record_trust_decisions", side_effect=error),
        pytest.raises(HTTPException) as exc_info,
    ):
        approve_project_endpoint(
            project_id=TEST_PROJECT_ID,
            payload=mock_payload,
            user_id=TEST_USER_ID,
            db=mock_db_session,
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == detail
    mock_get_trusts.assert_not_called()


@patch("flip_api.project_services.approve_project.get_trusts")
@patch(
    "flip_api.project_services.approve_project.record_trust_decisions",
    return_value=TrustDecisionOutcome(project_status=ProjectStatus.APPROVED, activated_trust_ids=[]),
)
@patch("flip_api.project_services.approve_project.decision_maker_for", return_value=DecisionMaker.HUB)
def test_approve_project_endpoint_never_asks_get_trusts_for_an_empty_list(
    mock_decision_maker_for,
    mock_record_trust_decisions,
    mock_get_trusts,
    mock_db_session,
    mock_payload,
    mock_staged_project,
):
    """``get_trusts`` with no ids returns every trust on the hub, which would dispatch imaging to all of them."""
    mock_db_session.get.return_value = mock_staged_project

    result = approve_project_endpoint(
        project_id=TEST_PROJECT_ID,
        payload=mock_payload,
        user_id=TEST_USER_ID,
        db=mock_db_session,
    )

    assert result == []
    mock_get_trusts.assert_not_called()


def test_approve_payload_rejects_a_trust_both_approved_and_declined():
    trust_id = uuid.uuid4()
    with pytest.raises(ValidationError, match="both approved and declined"):
        ApproveProjectBodyPayload(trusts=[trust_id], declined=[trust_id])


def test_approve_payload_declined_defaults_to_empty():
    """Callers that only ever approve (the smoke test, the demo seeder) keep sending ``trusts`` alone."""
    assert ApproveProjectBodyPayload(trusts=[uuid.uuid4()]).declined == []


@patch("flip_api.project_services.approve_project.logger")
@patch("flip_api.project_services.approve_project.decision_maker_for")
def test_approve_project_endpoint_no_permission(mock_decision_maker_for, mock_logger, mock_db_session, mock_payload):
    # Arrange
    mock_decision_maker_for.return_value = None

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        approve_project_endpoint(
            project_id=TEST_PROJECT_ID,
            payload=mock_payload,
            user_id=TEST_USER_ID,
            db=mock_db_session,
        )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert f"User with ID: {TEST_USER_ID} was unable to approve this project" == exc_info.value.detail
    # Every named trust was checked, one call each, by the per-trust rule (FLIP#1258).
    assert mock_decision_maker_for.call_count == len(TEST_TRUST_IDS)
    assert [call.args[1] for call in mock_decision_maker_for.call_args_list] == TEST_TRUST_IDS
    for call in mock_decision_maker_for.call_args_list:
        assert call.args[0] == TEST_USER_ID
    # The response body names no trust; which trusts the caller lacks authority over is
    # federation topology, and echoing it would make this endpoint a role-probe.
    for trust_id in TEST_TRUST_IDS:
        assert str(trust_id) not in str(exc_info.value.detail)
    # ...but the hub's own log does, so an operator can see which trusts refused.
    mock_logger.error.assert_called_once()
    assert all(str(trust_id) in mock_logger.error.call_args.args[0] for trust_id in TEST_TRUST_IDS)


@patch("flip_api.project_services.approve_project.logger")
@patch("flip_api.project_services.approve_project.get_trusts")
@patch("flip_api.project_services.approve_project.record_trust_decisions", return_value=APPROVED_OUTCOME)
@patch("flip_api.project_services.approve_project.decision_maker_for")
def test_partial_authority_approves_nothing(
    mock_decision_maker_for,
    mock_record_trust_decisions,
    mock_get_trusts,
    mock_logger,
    mock_db_session,
    mock_payload,
    mock_staged_project,
):
    """Authority at SOME of the named trusts is not enough — the call must approve none.

    This is the load-bearing property of site-scoped approval and the reason the check is
    done for the whole list before anything is written. If a partial list were approved,
    the caller would receive a success for a request only partly carried out, and the
    trusts they hold no authority over would have been approved anyway — by someone else's
    signature. `record_trust_decisions` commits the set in one transaction, so the refusal has
    to happen before it is reached.
    """
    mock_db_session.get.return_value = mock_staged_project
    # Authority over the first trust only.
    mock_decision_maker_for.side_effect = [DecisionMaker.HUB, None]

    with pytest.raises(HTTPException) as exc_info:
        approve_project_endpoint(
            project_id=TEST_PROJECT_ID,
            payload=mock_payload,
            user_id=TEST_USER_ID,
            db=mock_db_session,
        )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    # Nothing was written, and the refusal came before the approval transaction.
    mock_record_trust_decisions.assert_not_called()
    mock_get_trusts.assert_not_called()


@patch("flip_api.project_services.approve_project.logger")
@patch("flip_api.project_services.approve_project.get_trusts")
@patch("flip_api.project_services.approve_project.record_trust_decisions", return_value=STAGED_OUTCOME)
@patch("flip_api.project_services.approve_project.decision_maker_for", return_value=DecisionMaker.HUB)
def test_every_named_trust_is_authorised_at_that_trust(
    mock_decision_maker_for,
    mock_record_trust_decisions,
    mock_get_trusts,
    mock_logger,
    mock_db_session,
    mock_payload,
    mock_staged_project,
):
    """The trust id passed to the check must be the payload's, one call each.

    Guards the failure mode where a loop variable or the project id is passed by mistake:
    the endpoint would then ask about the wrong trust and could grant authority over a
    trust the caller holds none for.
    """
    mock_db_session.get.return_value = mock_staged_project
    mock_get_trusts.return_value = []

    approve_project_endpoint(
        project_id=TEST_PROJECT_ID,
        payload=mock_payload,
        user_id=TEST_USER_ID,
        db=mock_db_session,
    )

    assert [call.args[1] for call in mock_decision_maker_for.call_args_list] == TEST_TRUST_IDS


@patch("flip_api.project_services.approve_project.logger")
@patch("flip_api.project_services.approve_project.get_trusts")
@patch("flip_api.project_services.approve_project.record_trust_decisions", return_value=STAGED_OUTCOME)
def test_empty_trust_list_denies_rather_than_fails_open(
    mock_record_trust_decisions,
    mock_get_trusts,
    mock_logger,
    mock_db_session,
    mock_staged_project,
):
    """An empty payload must NOT be treated as "no trust needs authorising".

    `all()` over an empty sequence is True, and any implementation that reads as "deny if
    any named trust is unauthorised" passes vacuously here. The endpoint is therefore
    written to require authority at every named trust, and an empty list has none to
    satisfy — the same fail-open trap that `has_permissions([])` has.
    """
    mock_db_session.get.return_value = mock_staged_project
    empty_payload = MagicMock(spec=ApproveProjectBodyPayload)
    empty_payload.trusts = []
    empty_payload.declined = []

    with pytest.raises(HTTPException) as exc_info:
        approve_project_endpoint(
            project_id=TEST_PROJECT_ID,
            payload=empty_payload,
            user_id=TEST_USER_ID,
            db=mock_db_session,
        )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    mock_record_trust_decisions.assert_not_called()


@patch("flip_api.project_services.approve_project.logger")
@patch("flip_api.project_services.approve_project.record_trust_decisions", return_value=STAGED_OUTCOME)
@patch("flip_api.project_services.approve_project.decision_maker_for", return_value=None)
def test_declining_needs_authority_at_the_trust(
    mock_decision_maker_for,
    mock_record_trust_decisions,
    mock_logger,
    mock_db_session,
    mock_staged_project,
):
    """A decline is the site's decision as much as an approval, so it takes the same trust-scoped grant."""
    mock_db_session.get.return_value = mock_staged_project
    decline_only = MagicMock(spec=ApproveProjectBodyPayload)
    decline_only.trusts = []
    decline_only.declined = [DECLINED_TRUST_ID]

    with pytest.raises(HTTPException) as exc_info:
        approve_project_endpoint(
            project_id=TEST_PROJECT_ID,
            payload=decline_only,
            user_id=TEST_USER_ID,
            db=mock_db_session,
        )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert [call.args[1] for call in mock_decision_maker_for.call_args_list] == [DECLINED_TRUST_ID]
    mock_record_trust_decisions.assert_not_called()


@patch("flip_api.project_services.approve_project.record_trust_decisions", return_value=STAGED_OUTCOME)
@patch("flip_api.project_services.approve_project.decision_maker_for", return_value=DecisionMaker.HUB)
def test_a_decline_only_call_is_not_refused_as_empty(
    mock_decision_maker_for,
    mock_record_trust_decisions,
    mock_db_session,
    mock_staged_project,
):
    """The empty-list refusal counts declined trusts: a call that only declines names a trust."""
    mock_db_session.get.return_value = mock_staged_project
    decline_only = MagicMock(spec=ApproveProjectBodyPayload)
    decline_only.trusts = []
    decline_only.declined = [DECLINED_TRUST_ID]

    result = approve_project_endpoint(
        project_id=TEST_PROJECT_ID,
        payload=decline_only,
        user_id=TEST_USER_ID,
        db=mock_db_session,
    )

    assert result == []
    mock_record_trust_decisions.assert_called_once()


@patch("flip_api.project_services.approve_project.logger")
@patch("flip_api.project_services.approve_project.decision_maker_for", return_value=DecisionMaker.HUB)
def test_approve_project_endpoint_project_not_found(
    mock_decision_maker_for,  # Patched with return_value=True
    mock_logger,
    mock_db_session,
    mock_payload,
):
    # Arrange
    mock_db_session.get.return_value = None  # Project not found

    # Act & Assert
    with pytest.raises(HTTPException, match="does not exist") as exc_info:
        approve_project_endpoint(
            project_id=TEST_PROJECT_ID,
            payload=mock_payload,
            user_id=TEST_USER_ID,
            db=mock_db_session,
        )

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    assert f"Project ID: {str(TEST_PROJECT_ID)} does not exist" == exc_info.value.detail
    mock_db_session.get.assert_called_once_with(Projects, TEST_PROJECT_ID)
    mock_logger.error.assert_called_once_with(f"Project with ID {TEST_PROJECT_ID} not found for approval.")


@patch("flip_api.project_services.approve_project.logger")
@patch("flip_api.project_services.approve_project.decision_maker_for", return_value=DecisionMaker.HUB)
def test_approve_project_endpoint_project_not_staged(
    mock_decision_maker_for,
    mock_logger,
    mock_db_session,
    mock_payload,
    mock_staged_project,  # Use fixture but change status
):
    # Arrange
    mock_staged_project.status = ProjectStatus.UNSTAGED  # Staged or approved projects take decisions
    mock_db_session.get.return_value = mock_staged_project

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        approve_project_endpoint(
            project_id=TEST_PROJECT_ID,
            payload=mock_payload,
            user_id=TEST_USER_ID,
            db=mock_db_session,
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert "Unable to approve the project as it has not been staged" == exc_info.value.detail
    mock_logger.error.assert_called_once_with(
        f"Project {TEST_PROJECT_ID} is not staged, cannot record trust decisions."
    )


@patch("flip_api.project_services.approve_project.logger")
@patch("flip_api.project_services.approve_project.get_trusts")
@patch("flip_api.project_services.approve_project.record_trust_decisions")
@patch("flip_api.project_services.approve_project.decision_maker_for", return_value=DecisionMaker.HUB)
def test_approve_project_endpoint_commit_status_fails(
    mock_decision_maker_for,
    mock_record_trust_decisions,
    mock_get_trusts,  # This is the get_trusts function
    mock_logger,
    mock_db_session,
    mock_payload,
    mock_staged_project,
):
    # Arrange
    mock_db_session.get.return_value = mock_staged_project

    commit_error = Exception("DB Commit Error")
    mock_record_trust_decisions.side_effect = commit_error

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        approve_project_endpoint(
            project_id=TEST_PROJECT_ID,
            payload=mock_payload,
            user_id=TEST_USER_ID,
            db=mock_db_session,
        )

    assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert exc_info.value.detail == "Internal server error"
    assert "DB Commit Error" not in exc_info.value.detail


@patch("flip_api.project_services.approve_project.logger")
@patch("flip_api.project_services.approve_project.get_trusts")
@patch("flip_api.project_services.approve_project.record_trust_decisions", return_value=APPROVED_OUTCOME)
@patch("flip_api.project_services.approve_project.decision_maker_for", return_value=DecisionMaker.HUB)
def test_approve_project_endpoint_fetch_trusts_exec_fails(
    mock_decision_maker_for,
    mock_record_trust_decisions,
    mock_get_trusts,  # This is the get_trusts function
    mock_logger,
    mock_db_session,
    mock_payload,
    mock_staged_project,
):
    # Arrange
    mock_db_session.get.return_value = mock_staged_project

    trust_exec_error = Exception("Trust exec Error")
    mock_get_trusts.side_effect = trust_exec_error

    # First commit for project status update is successful
    mock_db_session.commit.side_effect = [None, Exception("This second commit should not be reached")]

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        approve_project_endpoint(
            project_id=TEST_PROJECT_ID,
            payload=mock_payload,
            user_id=TEST_USER_ID,
            db=mock_db_session,
        )

    assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert exc_info.value.detail == "Internal server error"
    assert str(trust_exec_error) not in exc_info.value.detail

    # The traceback (and with it the exception text) goes to the log, not the response body.
    mock_logger.exception.assert_called_with(f"Unhandled error during project approval for {TEST_PROJECT_ID}")
