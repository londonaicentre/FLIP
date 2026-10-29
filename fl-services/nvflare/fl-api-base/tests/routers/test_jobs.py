# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
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

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import status
from nvflare.fuel.flare_api.api_spec import AuthenticationError, JobNotFound

from fl_api.app import app
from fl_api.core.dependencies import get_session, get_studies
from fl_api.utils.schemas import TargetType
from fl_api.utils.study_sessions import StudySessions


@pytest.fixture(autouse=True)
def override_session(client):
    """Override get_session with a conditional MagicMock that matches FLARE behavior."""
    fake_session = MagicMock()
    existing_jobs = {"1234": "some_job"}

    # --- Job methods ---
    def abort_side_effect(job_id: str):
        if job_id not in existing_jobs:
            raise JobNotFound(f"Job {job_id} not found.")
        # /abort_job ignores abort_job's return value (it builds JobMetadata directly).
        return None

    def delete_side_effect(job_id: str):
        if job_id not in existing_jobs:
            raise JobNotFound(f"Job {job_id} not found.")
        return {"status": "success", "info": f"Job {job_id} deleted."}

    def reset_errors_side_effect(job_id: str):
        if job_id not in existing_jobs:
            raise JobNotFound(f"Job {job_id} not found.")
        return None

    def list_jobs_side_effect(detailed=False, limit=None, id_prefix=None, name_prefix=None, reverse=False):
        # mimic realistic FLARE output: keys are job_id/job_name, status values are RunStatus strings
        jobs = [
            {"job_id": "1234", "job_name": "training_round_1", "status": "FINISHED:COMPLETED"},
            {"job_id": "5678", "job_name": "training_round_2", "status": "RUNNING"},
        ]
        if limit:
            jobs = jobs[:limit]
        return jobs

    fake_session.abort_job.side_effect = abort_side_effect
    fake_session.delete_job.side_effect = delete_side_effect
    fake_session.reset_errors.side_effect = reset_errors_side_effect
    fake_session.list_jobs.side_effect = list_jobs_side_effect

    app.dependency_overrides[get_session] = lambda: fake_session
    yield fake_session  # ✅ yield it so tests can access it
    app.dependency_overrides.clear()


@pytest.fixture
def study_sessions():
    """The study sessions the routes open, by study (FLIP#1678)."""
    return {}


@pytest.fixture(autouse=True)
def override_studies(override_session, study_sessions):
    """The real StudySessions around the fake default session; each study session is a MagicMock."""
    studies = StudySessions(
        override_session, lambda study: study_sessions.setdefault(study, MagicMock()), Path("/nope")
    )
    app.dependency_overrides[get_studies] = lambda: studies
    return studies  # override_session clears the overrides


def test_list_jobs_success(client):
    response = client.get("/list_jobs")
    assert response.status_code == status.HTTP_200_OK

    jobs = response.json()
    assert isinstance(jobs, list)
    assert jobs == [
        {"job_id": "1234", "status": "FINISHED"},
        {"job_id": "5678", "status": "RUNNING"},
    ]


def test_list_jobs_with_limit(client):
    response = client.get("/list_jobs", params={"limit": 1})
    assert response.status_code == status.HTTP_200_OK

    jobs = response.json()
    assert len(jobs) == 1
    assert jobs[0]["job_id"] == "1234"


def test_list_jobs_internal_error(client, override_session):
    # Simulate an unexpected failure
    override_session.list_jobs.side_effect = Exception("DB connection lost")

    response = client.get("/list_jobs")
    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR


def test_reset_errors_success(client):
    response = client.post("/reset_errors", params={"job_id": "1234"})
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "success"
    assert "reset" in data["info"].lower()


def test_reset_errors_job_not_found(client):
    response = client.post("/reset_errors", params={"job_id": "9999"})
    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_abort_job_success(client):
    response = client.delete("/abort_job/1234")
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"job_id": "1234", "status": "STOPPED"}


def test_abort_job_not_found(client):
    response = client.delete("/abort_job/9999")
    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_delete_job_success(client):
    response = client.delete("/delete_job/1234")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "success"
    assert "deleted" in data["info"]


def test_delete_job_not_found(client):
    response = client.delete("/delete_job/9999")
    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_submit_job_rejects_non_uuid(client):
    """Production submit is UUID-strict: FastAPI rejects a non-UUID job_folder with 422."""
    response = client.post("/submit_job/not-a-uuid")
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_submit_job_accepts_uuid(client, override_session):
    """A UUID job_folder is accepted and forwarded to the session unchanged."""
    override_session.submit_job.return_value = "job-1234"
    model_id = "c7f72374-0752-473f-a28f-592e4d8b7a47"

    response = client.post(f"/submit_job/{model_id}")

    assert response.status_code == status.HTTP_200_OK
    override_session.submit_job.assert_called_once_with(model_id)


# --- NVFLARE studies: one per project (FLIP#1678) ------------------------------------------------

MODEL_ID = "c7f72374-0752-473f-a28f-592e4d8b7a47"


def test_submit_with_study_registers_then_submits_in_the_study_session(client, override_studies, study_sessions):
    order = MagicMock()
    with (
        patch("fl_api.routers.jobs.job_sites", return_value=["Trust_1"]),
        patch.object(override_studies, "register", side_effect=lambda *a: order.register(*a)),
    ):
        study_sessions["p1"] = MagicMock()
        study_sessions["p1"].submit_job.side_effect = lambda folder: (order.submit(folder), "job-1")[1]

        response = client.post(f"/submit_job/{MODEL_ID}", params={"study": "p1"})

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == "job-1"
    assert [c[0] for c in order.mock_calls] == ["register", "submit"]
    order.register.assert_called_once_with("p1", ["Trust_1"])
    order.submit.assert_called_once_with(MODEL_ID)


def test_submit_without_study_uses_the_default_session(client, override_session, study_sessions):
    override_session.submit_job.return_value = "job-0"

    response = client.post(f"/submit_job/{MODEL_ID}")

    assert response.json() == "job-0"
    assert study_sessions == {}


def test_list_jobs_merges_default_and_named_studies(client, study_sessions):
    study_sessions["p1"] = MagicMock()
    study_sessions["p1"].list_jobs.return_value = [{"job_id": "s1", "status": "RUNNING"}]

    response = client.get("/list_jobs", params=[("study", "p1")])

    assert response.status_code == status.HTTP_200_OK
    assert [j["job_id"] for j in response.json()] == ["1234", "5678", "s1"]


def test_list_jobs_skips_an_unknown_study(client, override_studies):
    with patch.object(
        override_studies,
        "_factory",
        side_effect=AuthenticationError("unknown study", auth_code="AUTH_UNKNOWN_STUDY"),
    ):
        response = client.get("/list_jobs", params=[("study", "gone")])

    assert response.status_code == status.HTTP_200_OK
    assert [j["job_id"] for j in response.json()] == ["1234", "5678"]


def test_list_jobs_forgets_a_cached_study_the_server_has_lost(client, study_sessions):
    # Final review #3: a server restarted without its study registry rejects the cached session's re-login. The study
    # holds no jobs any more; the next list must log in afresh rather than fail on the dead session for good.
    study_sessions["p1"] = MagicMock()
    study_sessions["p1"].list_jobs.side_effect = AuthenticationError("unknown study", auth_code="AUTH_UNKNOWN_STUDY")

    first = client.get("/list_jobs", params=[("study", "p1")])
    lost = study_sessions.pop("p1")
    study_sessions["p1"] = MagicMock()
    study_sessions["p1"].list_jobs.return_value = [{"job_id": "s1", "status": "RUNNING"}]
    second = client.get("/list_jobs", params=[("study", "p1")])

    assert first.status_code == status.HTTP_200_OK
    assert [j["job_id"] for j in first.json()] == ["1234", "5678"]
    lost.close.assert_called()
    assert [j["job_id"] for j in second.json()] == ["1234", "5678", "s1"]


def test_list_jobs_fails_on_any_other_error(client, study_sessions):
    # A partial list would read as "the job is gone" to the hub's watchdog, which then fails a live model.
    study_sessions["p1"] = MagicMock()
    study_sessions["p1"].list_jobs.side_effect = Exception("server unreachable")

    response = client.get("/list_jobs", params=[("study", "p1")])

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR


def test_abort_job_uses_the_named_study_session(client, override_session, study_sessions):
    study_sessions["p1"] = MagicMock()

    response = client.delete("/abort_job/s1", params={"study": "p1"})

    assert response.status_code == status.HTTP_200_OK
    study_sessions["p1"].abort_job.assert_called_once_with("s1")
    override_session.abort_job.assert_not_called()


def test_download_job_uses_the_named_study_session(client, study_sessions):
    study_sessions["p1"] = MagicMock()
    study_sessions["p1"].download_job_result.return_value = "/tmp/result"

    response = client.get("/download_job/s1", params={"study": "p1"})

    assert response.json() == "/tmp/result"


def test_list_jobs_fails_on_an_authentication_error_that_is_not_a_lost_study(client, study_sessions):
    # Only "unknown study" means the study is gone; a refused credential must surface, not read as "no jobs".
    study_sessions["p1"] = MagicMock()
    study_sessions["p1"].list_jobs.side_effect = AuthenticationError("bad token", auth_code="AUTH_FAILED")

    response = client.get("/list_jobs", params=[("study", "p1")])

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    study_sessions["p1"].close.assert_not_called()


def test_list_jobs_fails_when_the_default_session_is_refused(client, override_session):
    override_session.list_jobs.side_effect = AuthenticationError("unknown study", auth_code="AUTH_UNKNOWN_STUDY")

    response = client.get("/list_jobs")

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR


def test_show_errors_uses_the_named_study_session(client, override_session, study_sessions):
    study_sessions["p1"] = MagicMock()
    study_sessions["p1"].show_errors.return_value = {"site-1": {"ClientRunner": "boom"}}

    response = client.post(f"/1234/show_errors/{TargetType.CLIENT.value}/site-1,site-2", params={"study": "p1"})

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"site-1": {"ClientRunner": "boom"}}
    study_sessions["p1"].show_errors.assert_called_once_with("1234", TargetType.CLIENT, ["site-1", "site-2"])
    override_session.show_errors.assert_not_called()


def test_show_stats_defaults_to_every_target_in_the_default_session(client, override_session):
    override_session.show_stats.return_value = {"server": {"ServerRunner": {}}}

    response = client.post(f"/show_stats/{TargetType.SERVER.value}/1234")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"server": {"ServerRunner": {}}}
    override_session.show_stats.assert_called_once_with("1234", TargetType.SERVER, None)
