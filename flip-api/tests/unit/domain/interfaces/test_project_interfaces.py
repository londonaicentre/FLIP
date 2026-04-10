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

from datetime import datetime
from typing import TypeVar
from uuid import UUID, uuid4

from pydantic import BaseModel

from flip_api.domain.interfaces.project import (
    IApprovedTrust,
    ICountResponse,
    IImagingImportStatus,
    IImagingProjectStatusParams,
    IImagingStatus,
    IImagingStatusResponse,
    IModelsInfoResponse,
    IProject,
    IProjectQuery,
    IProjectResponse,
    IReimportQuery,
    IReimportResponse,
    IReturnedProject,
    IUpdateXnatProfile,
)
from flip_api.domain.schemas.status import ModelStatus, ProjectStatus
from flip_api.utils.paging_utils import IPagedData, IPagedResponse

# Common timestamp used for testing
now = datetime.utcnow().isoformat(timespec="milliseconds")


class TestIProjectSchema:
    def test_valid_iproject(self):
        project_id = str(uuid4())
        owner_id = str(uuid4())
        project = IProject(
            id=project_id,
            name="Test Project",
            description="A test description",
            owner_id=owner_id,
            deleted=False,
            approved=True,
            creation_timestamp=now,
            status=ProjectStatus.APPROVED,
        )
        assert project.id == UUID(project_id)
        assert project.name == "Test Project"
        assert project.owner_id == UUID(owner_id)
        assert project.approved is True
        assert project.creation_timestamp == now
        assert project.status == ProjectStatus.APPROVED

    def test_iproject_optional_approved(self):
        project = IProject(
            id=str(uuid4()),
            name="P",
            description="d",
            owner_id=str(uuid4()),
            deleted=False,
            creation_timestamp=now,
            status=ProjectStatus.APPROVED,
        )
        assert project.approved is None  # Default for Optional field


class TestIProjectQuerySchema:
    def test_valid_iproject_query(self):
        query_id = str(uuid4())
        query = IProjectQuery(
            id=query_id,
            name="Age Query",
            query="SELECT * FROM table WHERE age > 50",  # Renamed from query
            trusts_queried=5,
            total_cohort=100,
        )
        assert query.id == UUID(query_id)
        assert query.name == "Age Query"
        assert query.query == "SELECT * FROM table WHERE age > 50"
        assert query.trusts_queried == 5
        assert query.total_cohort == 100

    def test_iproject_query_optional_fields(self):
        query = IProjectQuery(id=str(uuid4()), name="Q", query="Q_TEXT", trusts_queried=None, total_cohort=None)
        assert query.trusts_queried is None
        assert query.total_cohort is None


class TestIApprovedTrustSchema:
    def test_valid_iapproved_trust(self):
        trust_id = str(uuid4())
        trust = IApprovedTrust(id=trust_id, name="Test Trust", approved=True)
        assert trust.id == UUID(trust_id)
        assert trust.name == "Test Trust"
        assert trust.approved is True


class TestIProjectResponseSchema:
    def test_valid_iproject_response_with_query(self):
        project_id = uuid4()
        owner_id = uuid4()
        query_id = uuid4()

        response = IProjectResponse(
            id=project_id,
            name="P Resp",
            description="d",
            owner_id=owner_id,
            deleted=False,
            creation_timestamp=now,
            status=ProjectStatus.APPROVED,
            query=IProjectQuery(id=query_id, name="Q", query="QT"),
        )
        assert response.id == project_id
        assert response.query is not None
        assert response.query.id == query_id

    def test_valid_iproject_response_without_query(self):
        response = IProjectResponse(
            id=str(uuid4()),
            name="P Resp",
            description="d",
            owner_id=str(uuid4()),
            deleted=False,
            creation_timestamp=now,
            status=ProjectStatus.APPROVED,
        )
        assert response.query is None


class TestIReturnedProjectSchema:
    def test_valid_ireturned_project(self):
        user1_email = "user@gmail.com"
        user2_email = "user@test.com"
        returned_project = IReturnedProject(
            id=uuid4(),
            name="Ret P",
            description="d",
            owner_id=uuid4(),
            deleted=False,
            creation_timestamp=now,
            status=ProjectStatus.APPROVED,
            owner_email="owner@example.com",
            approved_trusts=[IApprovedTrust(id=uuid4(), name="T1", approved=True)],
            query=IProjectQuery(id=str(uuid4()), name="Q", query="QT"),
            users=[
                user1_email,
                user2_email,
            ],
        )
        assert returned_project.owner_email == "owner@example.com"
        assert len(returned_project.approved_trusts) == 1
        assert len(returned_project.users) == 2


class TestIPagedResponseSchema:
    TData = TypeVar("TData")

    class SampleData(BaseModel):
        value: int

    def test_valid_ipaged_response(self):
        data_list = [TestIPagedResponseSchema.SampleData(value=1), TestIPagedResponseSchema.SampleData(value=2)]
        paged_response = IPagedResponse[TestIPagedResponseSchema.SampleData](data=data_list, total_rows=10)
        assert len(paged_response.data) == 2
        assert paged_response.data[0].value == 1
        assert paged_response.total_rows == 10


class TestIPagedDataSchema:
    TData = TypeVar("TData")

    class SampleData(BaseModel):
        item: str

    def test_valid_ipaged_data(self):
        data_list = [TestIPagedDataSchema.SampleData(item="a"), TestIPagedDataSchema.SampleData(item="b")]
        paged_data = IPagedData[TestIPagedDataSchema.SampleData](
            page=1, page_size=10, total_pages=5, total_records=42, data=data_list
        )
        assert paged_data.page == 1
        assert paged_data.total_records == 42
        assert len(paged_data.data) == 2


class TestIModelsInfoResponseSchema:
    def test_valid_imodels_info_response(self):
        model_id = str(uuid4())
        owner_id = str(uuid4())
        info = IModelsInfoResponse(
            id=model_id,
            name="Clinical Model",
            description="Predicts outcomes",
            status=ModelStatus.TRAINING_STARTED,
            owner_id=owner_id,  # Renamed from owner_id
        )
        assert info.id == UUID(model_id)
        assert info.status == ModelStatus.TRAINING_STARTED
        assert info.owner_id == UUID(owner_id)


class TestICountResponseSchema:
    def test_valid_icount_response(self):
        response = ICountResponse(count=123)
        assert response.count == 123


# Schemas like IProjectDetails, IProjectApproval, IStageProjectRequest
# are assumed to be tested in test_projects_schemas.py if they are defined there.
# If they are moved here, their tests should also be moved/duplicated.


class TestIImagingStatusSchemas:
    def test_valid_iimaging_import_status(self):
        status = IImagingImportStatus(
            successful_count=10, failed_count=1, processing_count=2, queued_count=5, queue_failed_count=0
        )
        assert status.successful_count == 10

    def test_valid_iimaging_status_response(self):
        response = IImagingStatusResponse(
            project_creation_completed=True,
            import_status=IImagingImportStatus(
                successful_count=5, failed_count=0, processing_count=0, queued_count=0, queue_failed_count=0
            ),
            reimport_count=1,
        )
        assert response.project_creation_completed is True
        assert response.import_status.successful_count == 5
        assert response.reimport_count == 1

    def test_valid_iimaging_status_response_optional_fields(self):
        response = IImagingStatusResponse(project_creation_completed=False)
        assert response.import_status is None
        assert response.reimport_count is None

    def test_valid_iimaging_status(self):
        trust_id_val = str(uuid4())
        status = IImagingStatus(
            trust_id=trust_id_val,  # Renamed from trust_id
            trust_name="XNAT Trust",
            project_creation_completed=True,
        )
        assert status.trust_id == UUID(trust_id_val)
        assert status.trust_name == "XNAT Trust"


class TestIUpdateXnatProfileSchema:
    def test_valid_iupdate_xnat_profile(self):
        profile = IUpdateXnatProfile(email="xnatuser@example.com", enabled=True)
        assert profile.email == "xnatuser@example.com"
        assert profile.enabled is True


class TestIImagingProjectStatusParamsSchema:
    def test_valid_iimaging_project_status_params(self):
        project_id_val = str(uuid4())
        query_id_val = str(uuid4())
        params = IImagingProjectStatusParams(project_id=project_id_val, query_id=query_id_val)
        assert params.project_id == UUID(project_id_val)
        assert params.query_id == UUID(query_id_val)


class TestIReimportQuerySchema:
    def test_valid_ireimport_query(self):
        query_id_val = str(uuid4())
        xnat_project_id_val = str(uuid4())  # Assuming this is also a UUID string
        trust_id_val = str(uuid4())
        reimport_q = IReimportQuery(
            query_id=query_id_val,
            query="SELECT * FROM reimport_source",
            xnat_project_id=xnat_project_id_val,
            last_reimport=now,
            trust_id=trust_id_val,
            trust_endpoint="http://trust.example.com/api",
            trust_name="Reimport Trust",
        )
        assert reimport_q.query_id == UUID(query_id_val)
        assert reimport_q.xnat_project_id == UUID(xnat_project_id_val)
        assert reimport_q.trust_id == UUID(trust_id_val)


class TestIReimportResponseSchema:
    def test_valid_ireimport_response(self):
        xnat_project_id_val = str(uuid4())
        trust_id_val = str(uuid4())
        response = IReimportResponse(
            xnat_project_id=xnat_project_id_val, trust_id=trust_id_val, trust_name="Responding Trust", status=200
        )
        assert response.xnat_project_id == UUID(xnat_project_id_val)
        assert response.trust_id == UUID(trust_id_val)
        assert response.status == 200
