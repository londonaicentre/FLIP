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

"""Integration coverage of the model-page ``trusts`` field (POST /api/step/model/{id}).

``retrieve_model`` reports the run's trusts from the **latest FL job's**
``fl_job_trust`` roster via a scalar subquery (order by ``created`` desc, limit
1). That selection is pure SQL — invisible to the mocked-session unit tests —
so the excluded-approved-trust and latest-of-two-jobs behaviours are pinned
here against the throwaway Postgres.
"""

from datetime import datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from flip_api.db.models.main_models import FLJob
from tests.integration.conftest import override_verify_token_as
from tests.integration.test_all_models_endpoint import (
    _add_model,
    _add_project,
    _add_trust,
    _approve_trust_for_model,
    _dispatch_run,
)

STEP_MODEL_URL = "/api/step/model"


def test_model_page_reports_only_the_latest_jobs_roster(
    client: TestClient, session, project_factory, model_factory, trust_factory
):
    """The model page's trusts are the newest dispatch roster — not the approved
    pool, and not an earlier run's roster."""
    user_id = uuid4()
    project = _add_project(session, project_factory, owner_id=user_id, name="Stroke triage")
    model = _add_model(session, model_factory, project_id=project.id, owner_id=user_id, name="stroke-v1")

    gstt = _add_trust(session, trust_factory, name="Guy's & St Thomas'", code="GSTT")
    kch = _add_trust(session, trust_factory, name="King's College Hospital", code="KCH")
    excluded = _add_trust(session, trust_factory, name="Excluded Trust", code="EXC")
    for trust in (gstt, kch, excluded):
        _approve_trust_for_model(session, model_id=model.id, trust_id=trust.id)

    # First run went to GSTT alone; a later re-initiation went to GSTT + KCH.
    # Explicit timestamps make "latest" deterministic.
    older, newer = datetime(2026, 1, 1), datetime(2026, 1, 1) + timedelta(hours=1)
    first_job = _dispatch_run(session, model_id=model.id, trusts=[gstt])
    second_job = _dispatch_run(session, model_id=model.id, trusts=[gstt, kch])
    for job, created in ((first_job, older), (second_job, newer)):
        session.get(FLJob, job.id).created = created
    session.commit()

    override_verify_token_as(user_id)
    response = client.post(f"{STEP_MODEL_URL}/{model.id}")

    assert response.status_code == 200
    assert {t["code"] for t in response.json()["trusts"]} == {"GSTT", "KCH"}


def test_undispatched_model_page_reports_no_trusts(
    client: TestClient, session, project_factory, model_factory, trust_factory
):
    user_id = uuid4()
    project = _add_project(session, project_factory, owner_id=user_id, name="Fresh")
    model = _add_model(session, model_factory, project_id=project.id, owner_id=user_id, name="fresh-model")
    approved = _add_trust(session, trust_factory, name="Approved But Idle", code="ABI")
    _approve_trust_for_model(session, model_id=model.id, trust_id=approved.id)

    override_verify_token_as(user_id)
    response = client.post(f"{STEP_MODEL_URL}/{model.id}")

    assert response.status_code == 200
    assert response.json()["trusts"] == []
