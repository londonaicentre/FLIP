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

from datetime import datetime, timezone
from typing import Annotated, Optional
from uuid import UUID, uuid4

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlmodel import Field, Relationship, SQLModel

from flip_api.domain.schemas.actions import ModelAuditAction, ProjectAuditAction, TrustAuditAction
from flip_api.domain.schemas.file import FileUploadStatus
from flip_api.domain.schemas.status import (
    JobStatus,
    ModelStatus,
    NetStatus,
    ProjectStatus,
    TaskStatus,
    TaskType,
    TrustIntersectStatus,
    XNATImageStatus,
)
from flip_api.domain.schemas.types import FLBackend


# Tables
class FLNets(SQLModel, table=True):
    __tablename__ = "fl_nets"  # type: ignore
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(unique=True)
    endpoint: str = Field(unique=True)
    # FL backend ("nvflare"/"flower") this net runs. Set at seed time from FL_BACKEND
    # (seed_fl_nets) and canonical — never reconciled at runtime. A framework switch happens by
    # re-seeding (make restart-fl recreates flip-api). Non-null: every net has a declared backend
    # from creation. Mapped like the other enum columns (status, task_type, ...): SQLModel infers a
    # SQLAlchemy Enum column from FLBackend, so the DB persists the member name and reads return a
    # real FLBackend member. FLBackend is the one column whose member name (NVFLARE) differs from its
    # lowercase value (nvflare), so the DB stores the uppercase name while env/JSON/S3 use the value.
    fl_backend: FLBackend = Field(nullable=False)

    schedulers: list["FLScheduler"] = Relationship(back_populates="net")


class FLScheduler(SQLModel, table=True):
    __tablename__ = "fl_scheduler"  # type: ignore
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    net_id: UUID = Field(foreign_key="fl_nets.id", alias="netid")
    status: NetStatus = Field(default=NetStatus.AVAILABLE)
    job_id: UUID | None = Field(default=None, foreign_key="fl_job.id", alias="jobid")

    job: Optional["FLJob"] = Relationship(back_populates="scheduler")
    net: Optional["FLNets"] = Relationship(back_populates="schedulers")


class FLJobTrust(SQLModel, table=True):
    """Many-to-many link between FLJob and Trust: the trusts selected for a training run."""

    __tablename__ = "fl_job_trust"  # type: ignore
    fl_job_id: UUID = Field(foreign_key="fl_job.id", primary_key=True)
    trust_id: UUID = Field(foreign_key="trust.id", primary_key=True)


class FLJob(SQLModel, table=True):
    __tablename__ = "fl_job"  # type: ignore
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    model_id: UUID = Field(foreign_key="model.id")
    status: JobStatus = Field(default=JobStatus.QUEUED)
    created: Annotated[datetime, Field(default_factory=datetime.utcnow)]
    started: datetime | None = Field(default=None)
    completed: datetime | None = Field(default=None)
    # FL-backend-assigned job identifier, treated as an opaque string the hub never parses (it only persists
    # and equality-matches it). Format varies by backend: a UUID-like string for NVFLARE, a stringified
    # integer run-id for Flower. NULL until the job is submitted to the backend (see fl_service.submit_job).
    # Distinct from `id` above, which is the hub's own UUID primary key for the job record.
    fl_backend_job_id: str | None = None

    scheduler: Optional["FLScheduler"] = Relationship(back_populates="job")
    trusts: list["Trust"] = Relationship(link_model=FLJobTrust)


class FLMetrics(SQLModel, table=True):
    __tablename__ = "fl_metrics"  # type: ignore
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    # The trust this metric is attributed to (its id). fl_client_name is the raw
    # FL client identity as reported by the FL server (FL kit slot for NVFLARE,
    # SUPERNODE_NAME for Flower), kept for traceability.
    trust: UUID = Field(foreign_key="trust.id")
    fl_client_name: str = Field()
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
    # Set only for logs reported by an FL client; None for model-level (hub) logs.
    trust: UUID | None = Field(default=None, foreign_key="trust.id")
    fl_client_name: str | None = Field(default=None)
    # Free display/exception text. Null for typed event rows, whose text is
    # composed at serve time by log_rendering.render_log so wording changes
    # never require an FL-image rebuild. The log-XOR-event shape is enforced at
    # the Pydantic ingest boundary only — a DB CHECK constraint was considered
    # and deliberately deferred — so internal add_log callers must supply
    # exactly one of log/event_type (an all-NULL row renders as an empty line).
    log: str | None = Field(default=None)
    # Typed events (FLLogEvent values): round progress reported by the FL layer,
    # plus hub-emitted rows (QUEUE_POSITION). Stored as plain text — not a native
    # PG enum — so extending the vocabulary never needs an ALTER TYPE migration.
    # Event-specific facts (total_rounds, size_bytes, returned/expected) live in
    # the JSONB details column; global_round is first-class so round-scoped
    # queries can filter and order on it without reaching into the JSONB. All
    # three are null on legacy free-text rows.
    event_type: str | None = Field(default=None)
    global_round: int | None = Field(default=None)
    details: dict | None = Field(default=None, sa_column=Column("details", JSONB, nullable=True))


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
    # `user_id` is nullable because audit rows from background status
    # transitions (fl_scheduler) have no end-user context. Endpoint-driven
    # transitions still record the caller.
    __tablename__ = "models_audit"  # type: ignore
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    model_id: UUID | None = Field(default=None, foreign_key="model.id", index=True)
    action: ModelAuditAction = Field()
    user_id: UUID | None = Field(default=None)
    audit_date: Annotated[datetime, Field(default_factory=lambda: datetime.now(timezone.utc))]


class ProjectTrustIntersect(SQLModel, table=True):
    __tablename__ = "project_trust_intersect"  # type: ignore
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    project_id: UUID | None = Field(default=None, foreign_key="projects.id", index=True)
    trust_id: UUID | None = Field(default=None, foreign_key="trust.id", index=True)
    approved: bool = Field()
    approved_at: datetime | None = Field(default=None)


class Projects(SQLModel, table=True):
    __tablename__ = "projects"  # type: ignore
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field()
    description: str = Field()
    owner_id: UUID = Field()
    deleted: bool = Field(default=False)
    creation_timestamp: Annotated[datetime, Field(default_factory=datetime.utcnow)]
    status: ProjectStatus = Field(default=ProjectStatus.UNSTAGED)
    dicom_to_nifti: bool = Field(default=True)


class ProjectsAudit(SQLModel, table=True):
    __tablename__ = "projects_audit"  # type: ignore
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    project_id: UUID | None = Field(default=None, foreign_key="projects.id", index=True)
    action: ProjectAuditAction = Field()
    user_id: UUID = Field()
    audit_date: Annotated[datetime, Field(default_factory=lambda: datetime.now(timezone.utc))]


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
    # Logical FK to user_profile.user_id (not a DB-level FK). Set from the
    # authenticated caller on every cohort-query insert.
    created_by: UUID = Field(index=True)
    project_id: UUID | None = Field(default=None, foreign_key="projects.id", index=True)
    # Trust UUIDs the query was dispatched to at submit time. Canonical
    # "queried trusts" set — includes trusts that later errored or never
    # responded, so the per-trust UI can still render them (as error chips
    # or "running" respectively). Empty list until the query is submitted;
    # submit_cohort_query overwrites it with the dispatched set.
    queried_trust_ids: list[UUID] = Field(
        default_factory=list,
        sa_column=Column("queried_trust_ids", ARRAY(PG_UUID(as_uuid=True)), nullable=False),
    )


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
    # One aggregate row per query. The serialization lock in _aggregate_and_save_results
    # already prevents concurrent inserts; the UNIQUE constraint is a DB-level backstop so a
    # duplicate can never be created silently (a duplicate would let the read path pick an
    # arbitrary row). Non-nullable on purpose: every aggregate belongs to a query (the only
    # writer always sets it), and Postgres treats NULLs as distinct under UNIQUE — a nullable
    # FK would let multiple NULL-query_id rows slip past the constraint, so the invariant is
    # only fully encoded when the column is NOT NULL. See issue #579.
    query_id: UUID = Field(foreign_key="queries.id", unique=True)


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
    last_heartbeat: datetime | None = Field(default=None)
    # Added for issue #506 — the `trust` table is the sole trust registry.
    # `code` is the short display label (e.g. "GSTT"); `region` is the NHS
    # region/geography (e.g. "London"). Both stay nullable because they are
    # optional inputs to register_trust. `api_key_hash` is nullable only so a
    # row can briefly exist mid-insert; every registered trust has it set —
    # register_trust mints the key and stores its SHA-256 here.
    #
    # `created_at` is always set (register_trust stamps it).
    code: str | None = Field(default=None)
    region: str | None = Field(default=None)
    api_key_hash: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TrustsAudit(SQLModel, table=True):
    """Audit log for trust-registry mutations (register / delete).

    Captures lifecycle events on `trust`: admin-UI `POST /admin/trusts`,
    deploy-CLI `register_trust.py`, and deploy-CLI `delete_trust.py`. Rows
    survive a hard delete of the underlying trust — `trust_id` is a bare
    UUID with NO foreign key, so the audit row remains queryable after the
    `trust` row is gone (delete_trust.py hard-deletes; an FK would either
    block that delete or null this column out).

    `modified_by_user_id` is nullable because the deploy-CLI path runs on
    a bastion under operator IAM, not an authenticated FLIP user: both
    `register_trust.py` and `delete_trust.py` write NULL. The UI path
    (`admin_create_trust`) stamps the Cognito sub of the authenticated
    admin.

    `trust_name` is denormalised here on purpose: it lets audit queries
    show a human-readable name long after the trust row was hard-deleted.
    """

    __tablename__ = "trusts_audit"  # type: ignore
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    trust_id: UUID = Field(index=True)
    trust_name: str = Field()
    action: TrustAuditAction = Field()
    modified_by_user_id: UUID | None = Field(default=None)
    audit_date: Annotated[datetime, Field(default_factory=lambda: datetime.now(timezone.utc))]


class FLKitSlot(SQLModel, table=True):
    """Pool of pre-provisioned FL participant kits the hub assigns to joining trusts.

    Decouples the hub-side trust identity (``Trust.name``, arbitrary admin-chosen) from
    the FL participant identity (the CN baked into the kit's cert at provisioning time).
    Operators get matching ``services/<slot_name>/`` dirs on every net's workspace —
    one global slot row covers all nets, so the assignment doesn't need a net_id column.

    Lifecycle: rows are seeded from ``FL_KIT_SLOT_NAMES`` (one per pre-provisioned FL
    kit slot). ``POST /admin/trusts`` claims the next ``assigned_to_trust_id IS
    NULL`` row in the same transaction as the trust insert.
    """

    __tablename__ = "fl_kit_slot"  # type: ignore
    slot_name: str = Field(primary_key=True)
    slot_number: int = Field()
    assigned_to_trust_id: UUID | None = Field(default=None, foreign_key="trust.id", index=True)
    assigned_at: datetime | None = Field(default=None)


class TrustTask(SQLModel, table=True):
    __tablename__ = "trust_task"  # type: ignore
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    trust_id: UUID = Field(foreign_key="trust.id", index=True)
    task_type: TaskType = Field()
    payload: str = Field()  # JSON-serialized task data
    # The query this task belongs to, when the task type is COHORT_QUERY.
    # Lets the cohort-results UI distinguish "task still queued, trust hasn't
    # polled yet" (PENDING) from "trust is processing" (IN_PROGRESS) without
    # JSON-searching the payload. Nullable + no FK — other task types don't
    # belong to a query, and tasks created before this column existed have NULL.
    query_id: UUID | None = Field(default=None, index=True)
    status: TaskStatus = Field(default=TaskStatus.PENDING)
    result: str | None = Field(default=None)  # JSON-serialized result data
    needs_post_processing: bool = Field(default=False)
    retry_count: int = Field(default=0)  # Number of times this task has been retried via stale recovery
    created_at: Annotated[datetime, Field(default_factory=lambda: datetime.now(timezone.utc))]
    updated_at: datetime | None = Field(default=None)


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
    last_reimport: Annotated[datetime, Field(default_factory=lambda: datetime.now(timezone.utc))]
    reimport_count: int = Field(default=0)
