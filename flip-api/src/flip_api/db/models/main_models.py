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
from typing import Annotated, Optional
from uuid import UUID, uuid4

from sqlalchemy import JSON, Column
from sqlmodel import Field, Relationship, SQLModel

from flip_api.domain.schemas.actions import ModelAuditAction, ProjectAuditAction
from flip_api.domain.schemas.file import FileUploadStatus
from flip_api.domain.schemas.images import ImageType
from flip_api.domain.schemas.status import (
    JobStatus,
    ModelStatus,
    NetStatus,
    ProjectStatus,
    TrustIntersectStatus,
    XNATImageStatus,
)


# Tables
class FLNets(SQLModel, table=True):
    __tablename__ = "fl_nets"  # type: ignore
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(unique=True)
    endpoint: str = Field(unique=True)

    schedulers: list["FLScheduler"] = Relationship(back_populates="net")


class FLScheduler(SQLModel, table=True):
    __tablename__ = "fl_scheduler"  # type: ignore
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    net_id: UUID = Field(foreign_key="fl_nets.id", alias="netid")
    status: NetStatus = Field(default=NetStatus.AVAILABLE)
    job_id: UUID | None = Field(default=None, foreign_key="fl_job.id", alias="jobid")

    job: Optional["FLJob"] = Relationship(back_populates="scheduler")
    net: Optional["FLNets"] = Relationship(back_populates="schedulers")


class FLJob(SQLModel, table=True):
    __tablename__ = "fl_job"  # type: ignore
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    model_id: UUID = Field(foreign_key="model.id")
    status: JobStatus = Field(default=JobStatus.QUEUED)
    created: Annotated[datetime, Field(default_factory=datetime.utcnow)]
    started: datetime | None = Field(default=None)
    completed: datetime | None = Field(default=None)
    clients: list[str] = Field(sa_column=Column(JSON), default=[])
    fl_backend_job_id: str | None = None

    scheduler: Optional["FLScheduler"] = Relationship(back_populates="job")


class FLMetrics(SQLModel, table=True):
    __tablename__ = "fl_metrics"  # type: ignore
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    trust: str = Field()
    model_id: UUID = Field(foreign_key="model.id")
    global_round: int = Field()
    timestamp: datetime | None = Field(default_factory=datetime.utcnow)
    label: str = Field()
    result: float = Field()


class FLLogs(SQLModel, table=True):
    __tablename__ = "fl_logs"  # type: ignore
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    model_id: UUID = Field(foreign_key="model.id")
    log_date: Annotated[datetime | None, Field(default_factory=datetime.utcnow)]
    success: bool = Field()
    trust_name: str | None = Field(default=None, nullable=True)
    log: str = Field()


class Image(SQLModel, table=True):
    __tablename__ = "image"  # type: ignore
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    imageref: str = Field()
    effectivefrom: Annotated[datetime, Field(default_factory=datetime.utcnow)]
    model_id: UUID | None = Field(default=None, foreign_key="model.id")
    type: ImageType = Field()


class Model(SQLModel, table=True):
    __tablename__ = "model"  # type: ignore
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field()
    description: str = Field()
    status: ModelStatus = Field()
    deleted: bool = Field(default=False)
    project_id: UUID | None = Field(default=None, foreign_key="projects.id")
    owner_id: UUID = Field()
    creation_timestamp: Annotated[datetime, Field(default_factory=datetime.utcnow)]


class ModelTrustIntersect(SQLModel, table=True):
    __tablename__ = "model_trust_intersect"  # type: ignore
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    status: TrustIntersectStatus = Field()
    model_id: UUID | None = Field(default=None, foreign_key="model.id")
    trust_id: UUID | None = Field(default=None, foreign_key="trust.id")
    fl_client_endpoint: str | None = Field(default=None)


class ModelsAudit(SQLModel, table=True):
    __tablename__ = "models_audit"  # type: ignore
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    model_id: UUID | None = Field(default=None, foreign_key="model.id")
    action: ModelAuditAction = Field()
    user_id: UUID = Field()
    audit_date: Annotated[datetime, Field(default_factory=datetime.utcnow)]


class ProjectTrustIntersect(SQLModel, table=True):
    __tablename__ = "project_trust_intersect"  # type: ignore
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    project_id: UUID | None = Field(default=None, foreign_key="projects.id")
    trust_id: UUID | None = Field(default=None, foreign_key="trust.id")
    approved: bool = Field()


class Projects(SQLModel, table=True):
    __tablename__ = "projects"  # type: ignore
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field()
    description: str = Field()
    owner_id: UUID = Field()
    deleted: bool = Field(default=False)
    creation_timestamp: Annotated[datetime, Field(default_factory=datetime.utcnow)]
    status: ProjectStatus = Field(default=ProjectStatus.UNSTAGED)


class ProjectsAudit(SQLModel, table=True):
    __tablename__ = "projects_audit"  # type: ignore
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    project_id: UUID | None = Field(default=None, foreign_key="projects.id")
    action: ProjectAuditAction = Field()
    user_id: UUID = Field()
    audit_date: Annotated[datetime, Field(default_factory=datetime.utcnow)]


class ProjectUserAccess(SQLModel, table=True):
    __tablename__ = "project_user_access"  # type: ignore
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    project_id: UUID | None = Field(default=None, foreign_key="projects.id")
    user_id: UUID = Field()


class Queries(SQLModel, table=True):
    __tablename__ = "queries"  # type: ignore
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field()
    query: str = Field()
    created: Annotated[datetime, Field(default_factory=datetime.utcnow)]
    project_id: UUID | None = Field(default=None, foreign_key="projects.id")


class QueryResult(SQLModel, table=True):
    __tablename__ = "query_result"  # type: ignore
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    query_id: UUID = Field(default=None, foreign_key="queries.id")
    trust_id: UUID | None = Field(default=None, foreign_key="trust.id")
    data: str = Field()


class QueryStats(SQLModel, table=True):
    __tablename__ = "query_stats"  # type: ignore
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    stats: str = Field()
    stats_received: Annotated[datetime, Field(default_factory=datetime.utcnow)]
    query_id: UUID | None = Field(default=None, foreign_key="queries.id")


class SiteBanner(SQLModel, table=True):
    __tablename__ = "site_banner"  # type: ignore
    id: int = Field(default=1, primary_key=True)
    message: str
    link: str | None = None
    enabled: bool


class SiteConfig(SQLModel, table=True):
    __tablename__ = "site_config"  # type: ignore
    key: str = Field(primary_key=True, index=True)
    value: bool


class Trust(SQLModel, table=True):
    __tablename__ = "trust"  # type: ignore
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field()
    endpoint: str = Field()


class UploadedFiles(SQLModel, table=True):
    __tablename__ = "uploaded_files"  # type: ignore
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field()
    status: FileUploadStatus = Field()
    size: float = Field()
    type: str = Field()
    tag: str | None = Field(default=None)
    model_id: UUID | None = Field(default=None, foreign_key="model.id")


class XNATProjectStatus(SQLModel, table=True):
    __tablename__ = "xnat_project_status"  # type: ignore
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    xnat_project_id: UUID = Field()
    project_id: UUID | None = Field(default=None, foreign_key="projects.id")
    trust_id: UUID | None = Field(default=None, foreign_key="trust.id")
    retrieve_image_status: XNATImageStatus = Field()
    query_at_creation: UUID | None = Field(default=None)
    last_reimport: Annotated[datetime, Field(default_factory=datetime.utcnow)]
    reimport_count: int = Field(default=0)
