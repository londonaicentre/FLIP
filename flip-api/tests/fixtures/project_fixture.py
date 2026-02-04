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

import pytest


@pytest.fixture
def project_id():
    """Fixture for generating a project ID."""
    return uuid.uuid4()


@pytest.fixture
def mock_project(project_id, project_factory):
    """Fixture for creating a mock project."""
    try:
        project = project_factory.build()
        project.id = project_id
        project.deleted = False
        project.status = "APPROVED"
    except Exception as e:
        print(f"Error creating mock project: {e}")
    return project


@pytest.fixture
def create_project_data(session, mock_project, project_factory):
    """Fixture for creating project data."""
    try:
        project = project_factory.build()
        project.id = mock_project.id
        project.deleted = False
        project.status = "APPROVED"
        session.add(project)
        session.commit()
    except Exception as e:
        session.rollback()
        print(f"Error creating project data: {e}")
    yield project
    try:
        session.refresh(project)
        session.delete(project)
        session.commit()
    except Exception as e:
        session.rollback()
        print(f"Error creating project data: {e}")
    return project


@pytest.fixture
def project_with_approved_trusts(
    mock_db_session, project_id, project_trust_intersect_factory, project_factory, trust_factory
):
    """Fixture for creating a project with approved trusts."""
    try:
        trust = trust_factory.build()
        project = project_factory.build()
        project.id = project_id
        project_trust_intersect = project_trust_intersect_factory.build()
        project_trust_intersect.project_id = project.id
        project_trust_intersect.trust_id = trust.id
        project_trust_intersect.approved = True
        mock_db_session.add(trust)
        mock_db_session.commit()
        mock_db_session.add(project)
        mock_db_session.commit()
        mock_db_session.add(project_trust_intersect)
        mock_db_session.commit()
    except Exception as e:
        mock_db_session.rollback()
        print(f"Error creating project with approved trusts: {e}")
    yield project_trust_intersect
    try:
        mock_db_session.refresh(project_trust_intersect)
        mock_db_session.delete(project_trust_intersect)
        mock_db_session.commit()
    except Exception as e:
        mock_db_session.rollback()
        print(f"Error creating project with approved trusts: {e}")
    return project_trust_intersect
