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

"""Authorisation of the FL server's callbacks about one model (status, logs, metrics)."""

from typing import cast
from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy import Column
from sqlmodel import Session, select

from flip_api.auth.access_manager import authenticate_internal_service
from flip_api.db.database import get_session
from flip_api.db.models.main_models import FLJob, Model
from flip_api.domain.schemas.status import JobStatus, ModelStatus

# One answer for every refusal, so a caller cannot tell a model that does not exist from one it may not report on.
_REFUSAL = "Not authorised for this model."


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
    db: Session = Depends(get_session),
    _: None = Depends(authenticate_internal_service),
) -> None:
    """Refuse a callback about a model that has no job left to report on it.

    Args:
        model_id (UUID): The model named in the route's path.
        db (Session): The database session.

    Raises:
        HTTPException: 403 when the model has no reporting job.
    """
    if not reporting_jobs(model_id, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_REFUSAL)
