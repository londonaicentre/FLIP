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

"""``GET /api/trust`` counts each trust's queued and in-progress FL jobs (FLIP#1739).

The counts come from one grouped join of ``fl_job_trust`` to ``fl_job``, which a mocked
session cannot check, so it runs against the throwaway Postgres.
"""

from uuid import uuid4

from fastapi.testclient import TestClient

from flip_api.db.models.main_models import FLJob
from flip_api.domain.schemas.status import JobStatus, ProjectStatus
from tests.integration.conftest import admin_user, override_verify_token_as


def test_trust_list_counts_queued_and_in_progress_jobs_per_trust(
    client: TestClient, session, project_factory, model_factory, trust_factory
):
    owner_id = uuid4()
    project = project_factory.build(owner_id=owner_id, deleted=False, status=ProjectStatus.APPROVED)
    session.add(project)
    session.commit()
    both, one, none = (trust_factory.build(name=name, code=name) for name in ("BOTH", "ONE", "NONE"))
    session.add_all([both, one, none])
    session.commit()

    def add_job(status: JobStatus, trusts) -> None:
        model = model_factory.build(project_id=project.id, owner_id=owner_id, deleted=False)
        session.add(model)
        session.commit()
        session.add(FLJob(model_id=model.id, status=status, trusts=trusts))
        session.commit()

    add_job(JobStatus.IN_PROGRESS, [both, one])
    add_job(JobStatus.QUEUED, [both])
    add_job(JobStatus.QUEUED, [both])
    # Finished and deleted jobs are not counted.
    add_job(JobStatus.COMPLETED, [both, one, none])
    add_job(JobStatus.DELETED, [both, one, none])

    def counts_seen_by(user_id) -> dict:
        override_verify_token_as(user_id)
        response = client.get("/api/trust")
        assert response.status_code == 200, response.text
        # Other tests' seeded trusts may be listed too; only this test's three are checked.
        counts = {t["code"]: (t["jobs_queued"], t["jobs_in_progress"]) for t in response.json()}
        return {code: counts[code] for code in ("BOTH", "ONE", "NONE")}

    assert counts_seen_by(admin_user(session)) == {"BOTH": (2, 1), "ONE": (0, 1), "NONE": (0, 0)}
    # The counts span every project, so a caller who is not a hub admin gets none of them.
    assert counts_seen_by(uuid4()) == dict.fromkeys(("BOTH", "ONE", "NONE"), (None, None))
