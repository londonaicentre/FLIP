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

"""Integration coverage of two lookups that guard a write: both are SQL-shaped and pass silently under a mocked session.

``has_active_fl_job`` decides whether a model may be queued again, and ``_recorded_for_another_project`` whether a
trust's imaging project id may be recorded.
"""

from uuid import uuid4

import pytest

from flip_api.db.models.main_models import FLJob, XNATImageStatus
from flip_api.domain.schemas.status import JobStatus, ModelStatus, ProjectStatus
from flip_api.fl_services.services.fl_service import has_active_fl_job
from flip_api.private_services.imaging_notifications import _recorded_for_another_project
from flip_api.private_services.project_images_helpers import insert_status


@pytest.fixture
def project(session, user_factory, project_factory):
    project = project_factory.build(owner_id=user_factory().id, status=ProjectStatus.APPROVED, deleted=False)
    session.add(project)
    session.commit()
    return project


@pytest.fixture
def model(session, project, model_factory):
    model = model_factory.build(
        project_id=project.id, owner_id=project.owner_id, status=ModelStatus.INITIATED, deleted=False
    )
    session.add(model)
    session.commit()
    return model


@pytest.mark.parametrize(
    ("job_status", "active"),
    [
        (JobStatus.QUEUED, True),
        (JobStatus.IN_PROGRESS, True),
        (JobStatus.COMPLETED, False),
        (JobStatus.DELETED, False),
    ],
)
def test_a_model_has_an_active_job_only_while_one_is_queued_or_running(session, model, job_status, active):
    session.add(FLJob(model_id=model.id, status=job_status))
    session.commit()

    assert has_active_fl_job(model.id, session) is active


def test_another_models_job_does_not_count(session, model, model_factory):
    other = model_factory.build(project_id=model.project_id, owner_id=model.owner_id, deleted=False)
    session.add(other)
    session.flush()
    session.add(FLJob(model_id=other.id, status=JobStatus.IN_PROGRESS))
    session.commit()

    assert has_active_fl_job(model.id, session) is False


def test_an_imaging_id_is_on_record_elsewhere_only_for_another_trust_or_project(
    session, project, project_factory, trust_factory
):
    trust, other_trust = trust_factory.build(), trust_factory.build()
    other_project = project_factory.build(owner_id=project.owner_id, status=ProjectStatus.APPROVED, deleted=False)
    session.add_all([trust, other_trust, other_project])
    session.commit()
    xnat_project_id = uuid4()
    insert_status(
        trust_id=trust.id,
        xnat_project_id=xnat_project_id,
        project_id=project.id,
        status=XNATImageStatus.CREATED,
        db=session,
    )

    assert _recorded_for_another_project(xnat_project_id, trust.id, project.id, session) is False
    assert _recorded_for_another_project(xnat_project_id, trust.id, other_project.id, session) is True
    assert _recorded_for_another_project(xnat_project_id, other_trust.id, project.id, session) is True
    assert _recorded_for_another_project(uuid4(), other_trust.id, other_project.id, session) is False
