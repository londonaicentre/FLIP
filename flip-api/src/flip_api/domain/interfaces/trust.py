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

from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from flip_api.domain.schemas.users import CognitoUser

# Interfaces


class IBasicTrust(BaseModel):
    id: UUID
    name: str


class ITrustHealth(BaseModel):
    trust_id: UUID = Field(..., alias="trustId")
    trust_name: str = Field(..., alias="trustName")
    online: bool

    model_config = ConfigDict(
        populate_by_name=True,
    )


class ITrust(BaseModel):
    id: UUID
    name: str
    endpoint: str
    fl_client_endpoint: str | None = Field(default=None, description="FL Client Endpoint URL")


class ICreateImagingProject(BaseModel):
    """Represents a project on the central hub from which an imaging project is created on XNAT."""

    project_id: UUID  # This is the central hub project ID
    trust_id: UUID
    project_name: str  # This is the name of the project on the central hub
    query: str | None = None
    users: list[CognitoUser] = []
    dicom_to_nifti: bool = True


class ICreatedImagingUser(BaseModel):
    """Represents a user created on XNAT. Used to be called IImageUser in the old repo."""

    username: str
    encrypted_password: str
    email: EmailStr


class ICreatedImagingProject(BaseModel):
    """Represents a project created on XNAT. Used to be called IImageId in the old repo."""

    imaging_project_id: UUID
    name: str
    created_users: list[ICreatedImagingUser]
    # TODO Consider adding the below if we want to notify existing users they have been added to a new imaging project
    # added_users: list[User]


class ISesTemplateData(BaseModel):
    trust_name: str
    project_name: str
    project_id: UUID
    username: str
    password: str
