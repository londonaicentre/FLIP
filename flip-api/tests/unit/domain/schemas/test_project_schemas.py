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

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

# Models to be tested
from flip_api.domain.schemas.projects import (
    ProjectDetails,
    StageProjectRequest,
)

user_id_1 = uuid4()
user_id_2 = uuid4()
users = [user_id_1, user_id_2]


class TestProjectDetailsSchema:
    def test_valid_project_details(self):
        details = ProjectDetails(
            name="  Test Project  ",  # Should be trimmed
            description=" A test description. ",  # Should be trimmed
            users=users,
        )
        assert details.name.strip() == "Test Project"
        assert (
            details.description == "A test description."
        )  # Note: Pydantic's constr trims, so no leading/trailing space
        assert details.users == [
            user_id_1,
            user_id_2,
        ]

    def test_valid_project_details_empty_users_list(self):
        details = ProjectDetails(
            name="Project With No Users",
            users=[],  # Empty list is allowed
        )
        assert details.name == "Project With No Users"
        assert details.users == []

    def test_valid_project_details_no_description(self):
        details = ProjectDetails(name="Project No Desc", users=users)
        assert details.description is None  # Optional field defaults to None

    def test_valid_project_details_empty_description(self):
        details = ProjectDetails(
            name="Project Empty Desc",
            description="",  # Allowed by .allow("")
            users=users,
        )
        assert details.description == ""

    def test_missing_name(self):
        with pytest.raises(ValidationError):
            ProjectDetails(description="desc", users=users)

    def test_description_too_long(self):
        with pytest.raises(ValidationError) as exc_info:
            ProjectDetails(name="Test", description="b" * 251, users=users)
        assert any("String should have at most 250 characters" in e["msg"] for e in exc_info.value.errors())

    def test_users_list_invalid_guid(self):
        with pytest.raises(ValidationError):
            ProjectDetails(name="Test Project", users=[users[0], "not-a-guid"])

    def test_users_list_not_a_list(self):
        with pytest.raises(ValidationError):
            ProjectDetails(name="Test Project", users="not-a-list")


class TestStageProjectRequestSchema:
    def test_valid_stage_project_request(self):
        trust_id_1 = str(uuid4())
        trust_id_2 = str(uuid4())
        request_data = StageProjectRequest(trusts=[trust_id_1, trust_id_2])
        assert request_data.trusts == [UUID(trust_id_1), UUID(trust_id_2)]

    def test_stage_project_missing_trusts_field(self):  # Field itself is required
        with pytest.raises(ValidationError) as exc_info:
            StageProjectRequest.model_validate({})  # trusts field missing
        assert any(e["type"] == "missing" and "trusts" in e["loc"] for e in exc_info.value.errors())

    def test_stage_project_empty_trusts_list(self):  # List is present but empty
        with pytest.raises(ValidationError):
            StageProjectRequest(trusts=[])

    def test_stage_project_trusts_invalid_guid(self):
        with pytest.raises(ValidationError):
            StageProjectRequest(trusts=[str(uuid4()), "not-a-guid"])

    def test_stage_project_trusts_not_a_list(self):
        with pytest.raises(ValidationError):
            StageProjectRequest(trusts="not-a-list")
