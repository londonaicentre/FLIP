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

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from flip_api.domain.interfaces.project import IProjectQuery, IProjectResponse
from flip_api.domain.interfaces.trust import ITrust
from flip_api.domain.schemas.status import ProjectStatus, TaskType
from flip_api.main import app
from flip_api.step_functions_services.approve_project_step_function import (
    get_session,
    verify_token,
)

client = TestClient(app)


trust_id_1 = uuid4()
trust_id_2 = uuid4()


@pytest.fixture
def project_id():
    return str(uuid4())


@pytest.fixture
def request_body():
    return {"trusts": [str(trust_id_1), str(trust_id_2)]}


@pytest.fixture
def mock_trusts():
    return [
        ITrust(id=trust_id_1, name="Trust 1"),
        ITrust(
            id=trust_id_2,
            name="Trust 2",
            flClientEndpoint="https://trust2.endpoint/fl",
        ),
    ]


@pytest.fixture(autouse=True)
def override_dependencies():
    mock_session = MagicMock()
    user_id = uuid4()

    app.dependency_overrides[get_session] = lambda: mock_session
    app.dependency_overrides[verify_token] = lambda: user_id

    yield mock_session, user_id

    app.dependency_overrides = {}


@pytest.fixture(autouse=True)
def mock_project_row(mock_project):
    """Default every test's project to has_imaging=True so the existing assertions hold (FLIP#1071).

    Backed by the conftest-registered ``Projects`` factory rather than a SimpleNamespace: a stub that
    invents its own attributes keeps this suite green through a rename of the column production reads.
    """
    mock_project.has_imaging = True
    with patch(
        "flip_api.step_functions_services.approve_project_step_function.get_project_by_id",
        return_value=mock_project,
    ) as mock:
        yield mock


@patch("flip_api.step_functions_services.approve_project_step_function.approve_project_endpoint")
@patch(
    "flip_api.step_functions_services.approve_project_step_function.queue_imaging_creation",
    new_callable=AsyncMock,
)
def test_approve_project_success(
    mock_start_imaging,
    mock_approve_project,
    project_id,
    request_body,
    mock_trusts,
):
    mock_approve_project.return_value = mock_trusts
    mock_start_imaging.return_value = None  # since it's async, just return None

    response = client.post(f"/api/step/project/{project_id}/approve", json=request_body)

    assert response.status_code == 200
    data = response.json()

    assert data["projectId"] == project_id
    assert data["projectStatus"] == "APPROVED"
    assert data["successful"] is True
    assert data["trusts"]["processed"] == 2
    assert data["trusts"]["failed"] == 0
    assert data["trusts"]["succeeded"] == 2

    mock_approve_project.assert_called_once()
    assert mock_start_imaging.await_count == 2


@patch("flip_api.step_functions_services.approve_project_step_function.approve_project_endpoint")
def test_approve_project_passes_the_declined_trusts_to_the_decision_step(
    mock_approve_project,
    project_id,
):
    """The step function must hand the whole body on: rebuilding it from `trusts` alone would drop every decline
    while the UI reported them recorded."""
    mock_approve_project.return_value = []

    response = client.post(
        f"/api/step/project/{project_id}/approve",
        json={"trusts": [str(trust_id_1)], "declined": [str(trust_id_2)]},
    )

    assert response.status_code == 200
    payload = mock_approve_project.call_args.kwargs["payload"]
    assert (payload.trusts, payload.declined) == ([trust_id_1], [trust_id_2])


@patch("flip_api.step_functions_services.approve_project_step_function.approve_project_endpoint")
def test_approve_project_rejects_a_trust_both_approved_and_declined(mock_approve_project, project_id):
    response = client.post(
        f"/api/step/project/{project_id}/approve",
        json={"trusts": [str(trust_id_1)], "declined": [str(trust_id_1)]},
    )

    assert response.status_code == 422
    assert "both approved and declined" in response.text
    mock_approve_project.assert_not_called()


@patch("flip_api.step_functions_services.approve_project_step_function.approve_project_endpoint")
@patch(
    "flip_api.step_functions_services.approve_project_step_function.queue_imaging_creation",
    new_callable=AsyncMock,
)
def test_approve_project_with_failure_in_trust(
    mock_start_imaging,
    mock_approve_project,
    project_id,
    request_body,
    mock_trusts,
):
    # Simulate one trust failing
    async def failing_start(*args, **kwargs):
        trust = kwargs["trust"]
        if trust.name == "Trust 2":
            raise Exception("Imaging failed")
        return None

    mock_approve_project.return_value = mock_trusts
    mock_start_imaging.side_effect = failing_start

    response = client.post(f"/api/step/project/{project_id}/approve", json=request_body)

    assert response.status_code == 200
    data = response.json()

    assert data["successful"] is False
    assert data["trusts"]["processed"] == 2
    assert data["trusts"]["failed"] == 1
    assert data["trusts"]["succeeded"] == 1


@patch("flip_api.step_functions_services.approve_project_step_function.approve_project_endpoint")
def test_a_late_trust_approval_freezes_that_trusts_cohort_before_its_imaging(
    mock_approve_project, project_id, mock_trusts, override_dependencies, fake_idp
):
    """A trust approving an already-APPROVED project starts only itself (FLIP#1258), through the real fan-out.

    That fan-out must queue the late trust's own cohort snapshot (FLIP#857) ahead of its imaging: the row-level
    routes serve only the frozen snapshot, so a trust joining without one would refuse its imaging and training.
    """
    mock_session, _ = override_dependencies
    early_trust, late_trust = mock_trusts
    mock_approve_project.return_value = [late_trust]
    project = IProjectResponse(
        id=UUID(project_id),
        name="Approved earlier",
        query=IProjectQuery(id=uuid4(), name="Cohort", query="SELECT person_id FROM omop.person"),
        owner_id=uuid4(),
        status=ProjectStatus.APPROVED,
    )
    fan_out = "flip_api.trusts_services.start_project_imaging_creation"
    with (
        patch(f"{fan_out}.get_project", return_value=project),
        patch(f"{fan_out}.get_approved_trusts_for_project", return_value=[early_trust, late_trust]),
        patch(f"{fan_out}.get_users_with_access", return_value=[]),
    ):
        response = client.post(f"/api/step/project/{project_id}/approve", json={"trusts": [str(late_trust.id)]})

    assert response.status_code == 200
    assert response.json()["trusts"] == {"processed": 1, "succeeded": 1, "failed": 0}
    queued = [(call.args[0].task_type, call.args[0].trust_id) for call in mock_session.add.call_args_list]
    assert queued == [(TaskType.PERSIST_COHORT, late_trust.id), (TaskType.CREATE_IMAGING, late_trust.id)]


@patch("flip_api.step_functions_services.approve_project_step_function.approve_project_endpoint")
def test_approve_project_unexpected_exception_returns_generic_detail(
    mock_approve_project,
    project_id,
    request_body,
):
    """The catch-all 500 must not echo the underlying exception text into the response body (#906)."""
    mock_approve_project.side_effect = Exception("connection to db-host:5432 refused")

    response = client.post(f"/api/step/project/{project_id}/approve", json=request_body)

    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error"
    assert "db-host" not in response.json()["detail"]


@pytest.mark.parametrize("status_after", [ProjectStatus.STAGED, ProjectStatus.APPROVED])
@patch("flip_api.step_functions_services.approve_project_step_function.approve_project_endpoint")
def test_a_call_that_starts_no_trust_dispatches_no_imaging_and_reports_the_real_status(
    mock_approve_project,
    project_id,
    request_body,
    mock_project_row,
    status_after,
):
    """No trusts back from the decision step → nothing to start. The project may still be STAGED (nothing approved
    yet), or already APPROVED (a late decline, or a re-sent approval), so the status is read back, not assumed."""
    mock_approve_project.return_value = []
    mock_project_row.return_value.status = status_after

    response = client.post(f"/api/step/project/{project_id}/approve", json=request_body)

    assert response.status_code == 200
    data = response.json()

    assert data["message"] == "Trust decisions recorded; nothing to start"
    assert data["projectStatus"] == status_after


@patch("flip_api.step_functions_services.approve_project_step_function.approve_project_endpoint")
@patch("flip_api.step_functions_services.approve_project_step_function.queue_cohort_snapshot")
@patch(
    "flip_api.step_functions_services.approve_project_step_function.queue_imaging_creation",
    new_callable=AsyncMock,
)
def test_approve_project_skips_imaging_fan_out_when_project_has_no_imaging(
    mock_start_imaging,
    mock_freeze_cohort,
    mock_approve_project,
    project_id,
    request_body,
    mock_trusts,
    mock_project_row,
):
    """A tabular-only project is approved but no CREATE_IMAGING task is dispatched to any trust (FLIP#1071).

    Each trust still freezes the approved cohort (FLIP#857): training reads it through /cohort/dataframe, which
    serves only the frozen members, so a tabular project without a frozen membership could never train.
    """
    mock_approve_project.return_value = mock_trusts
    mock_project_row.return_value = SimpleNamespace(has_imaging=False)

    response = client.post(f"/api/step/project/{project_id}/approve", json=request_body)

    assert response.status_code == 200
    data = response.json()
    assert data["successful"] is True
    assert data["trusts"] == {"processed": 2, "succeeded": 2, "failed": 0}
    assert [detail["trust"] for detail in data["details"]] == ["Trust 1", "Trust 2"]
    assert "no imaging" in data["message"]
    assert data["projectStatus"] == "APPROVED"
    mock_approve_project.assert_called_once()  # the project still becomes APPROVED
    assert mock_start_imaging.await_count == 0
    assert [call.kwargs["trust"] for call in mock_freeze_cohort.call_args_list] == mock_trusts


@patch("flip_api.step_functions_services.approve_project_step_function.approve_project_endpoint")
@patch("flip_api.step_functions_services.approve_project_step_function.queue_cohort_snapshot")
def test_a_trust_whose_cohort_cannot_be_frozen_is_reported_as_failed(
    mock_freeze_cohort, mock_approve_project, project_id, request_body, mock_trusts, mock_project_row
):
    """A tabular-only project's snapshot dispatch reports per trust, as imaging does, so a failure is not silent."""
    mock_approve_project.return_value = mock_trusts
    mock_project_row.return_value = SimpleNamespace(has_imaging=False)
    mock_freeze_cohort.side_effect = [None, HTTPException(status_code=500, detail="Internal server error")]

    response = client.post(f"/api/step/project/{project_id}/approve", json=request_body)

    assert response.status_code == 200
    data = response.json()
    assert data["successful"] is False
    assert data["trusts"] == {"processed": 2, "succeeded": 1, "failed": 1}
    assert [detail["success"] for detail in data["details"]] == [True, False]


@patch("flip_api.step_functions_services.approve_project_step_function.approve_project_endpoint")
@patch(
    "flip_api.step_functions_services.approve_project_step_function.queue_imaging_creation",
    new_callable=AsyncMock,
)
def test_approve_project_leaves_a_missing_row_to_the_authorised_approval_path(
    mock_start_imaging, mock_approve_project, project_id, request_body, mock_trusts, mock_project_row
):
    """A missing row must not short-circuit: this route only authenticates.

    ``CAN_APPROVE_PROJECTS`` is checked inside ``approve_project_endpoint``, so refusing here first
    would answer "does this project exist?" for a caller who is not allowed to approve anything —
    404 for a missing project against 403 for a real one. The pre-approval read exists only to carry
    ``has_imaging`` across the commit, so it defaults and lets the authorised path own the error.
    """
    mock_project_row.return_value = None
    mock_approve_project.return_value = mock_trusts

    response = client.post(f"/api/step/project/{project_id}/approve", json=request_body)

    assert response.status_code != 404
    mock_approve_project.assert_called_once()


@patch("flip_api.step_functions_services.approve_project_step_function.approve_project_endpoint")
@patch(
    "flip_api.step_functions_services.approve_project_step_function.queue_imaging_creation",
    new_callable=AsyncMock,
)
def test_approve_project_reports_the_permission_refusal_whether_or_not_the_project_exists(
    mock_start_imaging, mock_approve_project, project_id, request_body, mock_project_row
):
    """The refusal a caller without CAN_APPROVE_PROJECTS sees must not depend on the row existing."""
    mock_approve_project.side_effect = HTTPException(status_code=403, detail="not allowed")

    statuses = []
    for row in (SimpleNamespace(has_imaging=True), None):
        mock_project_row.return_value = row
        statuses.append(client.post(f"/api/step/project/{project_id}/approve", json=request_body).status_code)

    assert statuses == [403, 403]
    assert mock_start_imaging.await_count == 0


@patch("flip_api.step_functions_services.approve_project_step_function.approve_project_endpoint")
@patch(
    "flip_api.step_functions_services.approve_project_step_function.queue_imaging_creation",
    new_callable=AsyncMock,
)
def test_approve_project_hands_the_identity_provider_to_the_imaging_fan_out(
    mock_start_imaging,
    mock_approve_project,
    project_id,
    request_body,
    mock_trusts,
    fake_idp,
):
    """The fan-out calls ``queue_imaging_creation``, a plain function with no ``Depends()`` of its own: the
    endpoint must resolve the provider once and hand it down. Left out, every CREATE_IMAGING task fails and
    the image pull sits at 0/0 (seen on the dev stack)."""
    mock_approve_project.return_value = mock_trusts

    response = client.post(f"/api/step/project/{project_id}/approve", json=request_body)

    assert response.status_code == 200
    assert response.json()["trusts"]["succeeded"] == 2
    assert mock_start_imaging.await_count == 2
    for call in mock_start_imaging.await_args_list:
        assert call.kwargs["idp"] is fake_idp
