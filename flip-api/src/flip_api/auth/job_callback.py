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

"""Authorisation of the FL server's callbacks about one model (status, logs, metrics).

Each job is dispatched with its own credential (``fl_service.start_training``), of which the hub keeps the
hash. The job's FL server presents it on every callback, so a job can report on its own model and no other.
"""

import hashlib
import hmac
from typing import cast
from uuid import UUID

from fastapi import Depends, HTTPException, Security, status
from fastapi.security.api_key import APIKeyHeader
from sqlalchemy import Column
from sqlmodel import Session, select

from flip_api.auth.access_manager import authenticate_internal_service
from flip_api.db.database import get_session
from flip_api.db.models.main_models import FLJob, Model
from flip_api.domain.schemas.status import JobStatus, ModelStatus

# Fixed on both sides: flip-utils sends it (flip.core.standard.JOB_CREDENTIAL_HEADER).
JOB_CREDENTIAL_HEADER = "X-Job-Credential"
_job_credential_header = APIKeyHeader(name=JOB_CREDENTIAL_HEADER, scheme_name="jobCredential", auto_error=False)

# One answer for every refusal, so a caller cannot tell a model that does not exist from one it may not report on.
_REFUSAL = "Not authorised for this model."


def hash_job_credential(credential: str) -> str:
    """SHA-256 hex digest of a job credential: a 256-bit random token, so a slow hash would add nothing."""
    return hashlib.sha256(credential.encode()).hexdigest()


def reporting_jobs(model_id: UUID, session: Session) -> list[FLJob]:
    """The model's jobs whose FL server may still report on it.

    Args:
        model_id (UUID): The model the callback names.
        session (Session): The database session.

    Returns:
        list[FLJob]: Every IN_PROGRESS job of the model. When there is none, the model's newest job if it is
        COMPLETED and the model is in ERROR. Otherwise empty.
    """
    jobs = session.exec(
        select(FLJob)
        .where(FLJob.model_id == model_id, FLJob.status != JobStatus.DELETED)
        .order_by(cast(Column, FLJob.created).desc())
    ).all()
    in_progress = [job for job in jobs if job.status == JobStatus.IN_PROGRESS]
    if in_progress:
        return in_progress
    # A run can outlive its job: Flower keeps training after one client's error has set the model to ERROR and
    # completed the job, then reports the other clients' errors, the later rounds and the uploaded results.
    if jobs and jobs[0].status == JobStatus.COMPLETED:
        model = session.get(Model, model_id)
        if model is not None and model.status == ModelStatus.ERROR:
            return [jobs[0]]
    return []


def authorise_job_callback(
    model_id: UUID,
    credential: str | None = Security(_job_credential_header),
    db: Session = Depends(get_session),
    _: None = Depends(authenticate_internal_service),
) -> None:
    """Refuse a callback unless it carries the credential of a job still reporting on the model.

    Args:
        model_id (UUID): The model named in the route's path.
        credential (str | None): The job credential from the request header.
        db (Session): The database session.

    Raises:
        HTTPException: 403 when the credential is missing or is not that of a reporting job of this model.
    """
    presented = hash_job_credential(credential or "")
    # Every candidate is compared, so timing does not say which job matched.
    matches = [hmac.compare_digest(presented, job.credential_hash or "") for job in reporting_jobs(model_id, db)]
    if not any(matches):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_REFUSAL)
