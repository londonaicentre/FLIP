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

# Job functions: upload, monitor, delete and handle jobs
from typing import List

from fastapi import APIRouter, Depends, status

from fl_api.core.dependencies import get_session
from fl_api.utils.flip_session import FLIP_Session
from fl_api.utils.schemas import JobMetaData

router = APIRouter()


@router.post("/submit_job/{job_folder}")
def submit_job(job_folder: str, session: FLIP_Session = Depends(get_session)) -> str:
    """
    Submits an existing job to the server.

    Args:
        job_folder (str): folder where the job is located.
        session (FLIP_Session): FLIP session instance.

    Returns:
        str: job ID if the system accepts the job.

    Raises:
        HTTPException: if the job submission fails due to any reason.
    """
    return session.submit_job(job_folder)


@router.get("/list_jobs", response_model=List[JobMetaData])
def list_jobs(
    session: FLIP_Session = Depends(get_session),
) -> List[JobMetaData]:
    """
    Returns a list of available jobs on the server.

    Args:
        session (FLIP_Session): FLIP session instance.

    Returns:
        List[JobMetaData]: a list of job meta data.

    Raises:
        HTTPException: if an error occurs while listing jobs.
    """
    return session.list_jobs()


@router.delete("/abort_job/{job_id}", status_code=status.HTTP_200_OK)
def abort_job(job_id: str, session: FLIP_Session = Depends(get_session)) -> dict:
    """Aborts job with provided job_id.

    Args:
        job_id (str): job ID.
        session (FLIP_Session): FLIP session instance.

    Raises:
        HTTPException: if the job is not found or if an error occurs during the abortion process.

    Returns:
        dict[str, str]: a dictionary containing the status and information about the job abortion operation.
    """
    session.abort_job(job_id)
    return {"status": "success", "info": f"Job {job_id} aborted."}
