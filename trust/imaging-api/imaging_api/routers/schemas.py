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
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, model_validator

from imaging_api.config import get_settings

PACS_ID = get_settings().PACS_ID
XNAT_PORT = get_settings().XNAT_PORT

# #########################
# Users
# #########################


class User(BaseModel):
    """Represents a user profile on XNAT."""

    lastModified: int
    username: str
    enabled: bool
    id: int
    secured: bool
    email: EmailStr
    verified: bool
    firstName: str
    lastName: str
    lastSuccessfulLogin: int | None = None


class CreateUser(BaseModel):
    """Represents a 'create user request' for XNAT."""

    username: str
    password: str
    email: EmailStr
    firstName: str
    lastName: str
    enabled: bool = True  # Default to True


class CreatedUser(BaseModel):
    """Represents a user created on XNAT."""

    username: str
    encrypted_password: str
    email: EmailStr


class UpdateUser(BaseModel):
    email: EmailStr
    enabled: bool


class CentralHubUser(BaseModel):
    id: UUID
    email: EmailStr
    is_disabled: bool = False


# #########################
# Projects
# #########################


class CreateProject(BaseModel):
    """Represents a 'create project request' for XNAT."""

    id: str  # This is the XNAT project ID
    secondary_id: str  # This is the central hub project ID
    name: str
    description: str = ""


class Project(BaseModel):
    """Represents a project on XNAT."""

    pi_firstname: str
    secondary_ID: str
    pi_lastname: str
    name: str
    description: str | None = None
    ID: str
    URI: str


class CreatedProject(BaseModel):
    """Represents a project created on XNAT."""

    ID: UUID
    name: str
    created_users: list[CreatedUser]
    added_users: list[User]


class CentralHubProject(BaseModel):
    """Represents a project on the central hub from which an imaging project is created on XNAT."""

    project_id: UUID  # This is the central hub project ID
    trust_id: UUID
    project_name: str
    query: str
    users: list[CentralHubUser] = []


class Subject(BaseModel):
    """
    Short representation of a subject on XNAT, it is the result of the REST API call

    ``GET f"{XNAT_URL}/data/projects/{project_id}/subjects"``
    """

    id: str = Field(..., alias="ID")
    label: str
    insert_date: str
    project: str
    insert_user: str
    uri: str = Field(..., alias="URI")


class Experiment(BaseModel):
    """
    Short representation of an experiment on XNAT, it is the result of the REST API call

    ``GET f"{XNAT_URL}/data/projects/{project_id}/experiments"``
    """

    id: str = Field(..., alias="ID")
    label: str
    date: str
    project: str
    insert_date: str
    xsiType: str = Field(..., alias="xsiType")
    uri: str = Field(..., alias="URI")
    subject_assessor_data_id: str | None = Field(None, alias="xnat:subjectassessordata/id")


# #########################
# Imaging
# #########################


class PacsStatus(BaseModel):
    """Represents the status of a PACS system."""

    pacs_id: int = Field(..., alias="pacsId")
    successful: bool
    ping_time: int = Field(..., alias="pingTime")
    created: int
    enabled: bool
    timestamp: int
    id: int
    disabled: int = Field(..., alias="disabled")


class Patient(BaseModel):
    id: str
    name: str
    sex: str


class Study(BaseModel):
    study_instance_uid: str = Field(..., alias="studyInstanceUid")
    study_description: str = Field(..., alias="studyDescription")
    accession_number: str = Field(..., alias="accessionNumber")
    study_date: str = Field(..., alias="studyDate")
    modalities_in_study: list[str] = Field(..., alias="modalitiesInStudy")
    referring_physician_name: str = Field(..., alias="referringPhysicianName")
    patient: Patient


class StudyQuery(BaseModel):
    """Represents a study query for PACS through XNAT DQR."""

    accession_number: str = Field(..., alias="accessionNumber")
    pacs_id: int = Field(default=PACS_ID, alias="pacsId")
    modality: str | None = Field(default="", alias="modality")


class ImportStudy(BaseModel):
    """Represents a study to be imported via DQR."""

    study_instance_uid: str = Field(..., alias="studyInstanceUid")
    accession_number: str = Field(..., alias="accessionNumber")
    relabel_map: dict[str, str] = Field(default={}, alias="relabelMap")

    def set_relabel_map(self):
        """Sets the relabel_map dictionary for subject and session."""
        self.relabel_map = {
            "Subject": str(uuid.uuid4()),  # Generate new UUID for Subject
            "Session": self.accession_number,
        }

    def __init__(self, **data):
        """Initializes the ImportStudy instance and sets the relabel_map."""
        super().__init__(**data)
        self.set_relabel_map()


class ImportStudyRequest(BaseModel):
    """Represents an image import request for DQR."""

    pacs_id: int = Field(default=PACS_ID, alias="pacsId")
    ae_title: str = Field(default="XNAT", alias="aeTitle")  # XNAT
    port: int = Field(default=XNAT_PORT, alias="port")
    project_id: str = Field(..., alias="projectId")
    force_import: bool = Field(default=True, alias="forceImport")
    studies: Annotated[list[ImportStudy], Field(..., min_length=1)]

    @model_validator(mode="before")
    @classmethod
    def deduplicate_and_parse_studies(cls, data):
        """Deduplicates and parses studies before validation."""
        # Make sure studies exist in raw input
        studies = data.get("studies", [])
        if studies:
            # Convert dicts to ImportStudy instances, if not already
            parsed_studies = [ImportStudy.model_validate(study) for study in studies]
            # Deduplicate by studyInstanceUid
            unique_studies = {study.study_instance_uid: study for study in parsed_studies}
            data["studies"] = list(unique_studies.values())
        return data


class ImportStudyResponse(BaseModel):
    """Represents the response from an import request."""

    # There are more fields in the response, but we only need these for now.
    # Also, most of the fields are also in the request, so we don't need to
    # repeat them here.
    id: int
    pacs_id: int = Field(alias="pacsId")
    status: str
    accession_number: str = Field(alias="accessionNumber")
    queued_time: int = Field(alias="queuedTime")
    created: int
    priority: int


# #########################
# Retrieval
# #########################


class ImportStatus(BaseModel):
    """Tracks the status of imported studies."""

    successful: list[str] = []
    failed: list[str] = []
    processing: list[str] = []
    queued: list[str] = []
    queue_failed: list[str] = []


class ImportStatusCount(BaseModel):
    """Tracks the status of imported studies."""

    successful_count: int = 0
    failed_count: int = 0
    processing_count: int = 0
    queued_count: int = 0
    queue_failed_count: int = 0


class ProjectRetrieval(BaseModel):
    """Represents a project retrieval."""

    project_creation_completed: bool = False
    import_status: ImportStatusCount = ImportStatusCount()


# #########################
# Download
# #########################


class DownloadImagesRequestData(BaseModel):
    """Represents a request to download images."""

    encrypted_central_hub_project_id: str
    accession_id: str


class DownloadImagesResponse(BaseModel):
    path: str


# ##########################
# Upload
# ##########################
class UploadDataRequest(BaseModel):
    """Represents a request to upload data."""

    encrypted_central_hub_project_id: str
    accession_id: str
    scan_id: str
    resource_id: str
    files: list[str]
    exist_ok: bool = False
