# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
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
from typing import Optional, Union

from fastapi import APIRouter, Depends, status
from nvflare.fuel.hci.client.fl_admin_api import TargetType

from fl_api.core.dependencies import get_session
from fl_api.utils.flip_session import FLIP_Session
from fl_api.utils.schemas import JobMetadata, JobStatus, normalize_status

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


@router.get("/download_job/{job_id}")
def download_job(job_id: str, session: FLIP_Session = Depends(get_session)) -> str:
    """
    Downloads the job result of the specified job ID.

    Args:
        job_id (str): job ID to be downloaded.
        session (FLIP_Session): FLIP session instance.

    Returns:
        str: location of the downloaded job result.
    """
    return session.download_job_result(job_id)


@router.get("/list_jobs", response_model=list[JobMetadata])
def list_jobs(
    detailed: bool = False,
    limit: Optional[int] = None,
    id_prefix: Union[str, None] = None,
    name_prefix: Union[str, None] = None,
    reverse: bool = False,
    session: FLIP_Session = Depends(get_session),
) -> list[JobMetadata]:
    """
    Returns a list of available jobs on the server, as the shared job-metadata contract.

    Args:
        detailed (bool, optional): whether extensive description is demanded. Defaults to False.
        limit (Optional[int], optional): maximum number of jobs to display. Defaults to None.
        id_prefix (str, optional): prefix for job ID search. Defaults to None.
        name_prefix (str, optional): prefix for the job NAME search. Defaults to None.
        reverse (bool, optional): if True, the order will be the reverse of submission time (otherwise it's the
        opposite). Defaults to False.
        session (FLIP_Session): FLIP session instance.

    Returns:
        list[JobMetadata]: the available jobs as normalized job-metadata contract items.

    Raises:
        HTTPException: if an error occurs while listing jobs.
    """
    raw_jobs = session.list_jobs(
        detailed=detailed,
        limit=limit,
        id_prefix=id_prefix,
        name_prefix=name_prefix,
        reverse=reverse,
    )
    # NVFLARE's list_jobs always includes job_id + status on every entry (verified against
    # nvflare 2.7.1 job_cmds.py, both the summary and detailed paths) — bracket access is intentional.
    return [JobMetadata(job_id=str(job["job_id"]), status=normalize_status(job["status"])) for job in raw_jobs]


@router.post("/{job_id}/show_errors/{target_type}")
@router.post("/{job_id}/show_errors/{target_type}/{targets}")
def show_errors(
    job_id: str,
    target_type: TargetType,
    targets: Optional[str] = None,
    session: FLIP_Session = Depends(get_session),
) -> dict:
    """
    Show processing errors of specified job on specified targets.

    Args:
        job_id (str): ID of the job
        target_type (str): type of target (server or client)
        targets: list of client names if target type is "client". All clients if not specified.
        session (FLIP_Session): FLIP session instance.

    Returns:
        dict: job errors (if any) on specified targets. The key of the dict is target name. The value is a dict of
        errors reported by different system components (ServerRunner or ClientRunner).
    """
    return session.show_errors(job_id, target_type, (targets.split(",") if targets else None))


@router.post("/show_stats/{target_type}/{job_id}")
@router.post("/show_stats/{target_type}/{targets}/{job_id}")
def show_stats(
    job_id: str,
    target_type: TargetType,
    targets: Optional[str] = None,
    session: FLIP_Session = Depends(get_session),
) -> dict:
    """
    Show processing stats of specified job on specified targets.

    Args:
        job_id (str): ID of the job
        target_type (str): type of target (server or client)
        targets: list of client names if target type is "client". All clients if not specified.
        session (FLIP_Session): FLIP session instance.

    Returns:
        dict: job stats on specified targets. The key of the dict is target name. The value is a dict of stats reported
        by different system components (ServerRunner or ClientRunner).

    Raises:
        HTTPException: if an error occurs while showing stats for the job.
    """
    return session.show_stats(job_id, target_type, (targets.split(",") if targets else None))


@router.post("/reset_errors", status_code=status.HTTP_200_OK)
def reset_errors(job_id: str, session: FLIP_Session = Depends(get_session)) -> dict:
    """
    Resets the errors of a specific job.

    Args:
        job_id (str): job ID.
        session (FLIP_Session): FLIP session instance.

    Returns:
        dict[str, str]: a dictionary containing the status and information about the error reset operation.

    Raises:
        HTTPException: if the job is not found or if an error occurs during the reset process.
    """
    session.reset_errors(job_id)
    return {"status": "success", "info": f"Errors for job {job_id} have been reset."}


@router.delete("/delete_job/{job_id}", status_code=status.HTTP_200_OK)
def delete_job(job_id: str, session: FLIP_Session = Depends(get_session)) -> dict:
    """
    Deletes a job from the server if it's not running.

    Args:
        job_id (str): job ID to be deleted.
        session (FLIP_Session): FLIP session instance.

    Raises:
        HTTPException: if the job ID does not exist or if an error occurs during the deletion process.

    Returns:
        dict[str, str]: a dictionary containing the status and information about the job deletion operation.
    """
    session.delete_job(job_id)
    return {"status": "success", "info": f"Job {job_id} deleted."}


@router.delete("/abort_job/{job_id}", status_code=status.HTTP_200_OK, response_model=JobMetadata)
def abort_job(job_id: str, session: FLIP_Session = Depends(get_session)) -> JobMetadata:
    """Aborts job with provided job_id.

    NVFLARE's ``abort_job`` is natively idempotent for already-terminal jobs ("if the job
    is already done, no effect"); a genuinely nonexistent ``job_id`` raises ``JobNotFound``,
    handled as a 404 by the app-level exception handler.

    Args:
        job_id (str): job ID.
        session (FLIP_Session): FLIP session instance.

    Raises:
        HTTPException: 404 if the job id does not exist.

    Returns:
        JobMetadata: the aborted job as a normalized job-metadata contract item.
    """
    session.abort_job(job_id)
    return JobMetadata(job_id=job_id, status=JobStatus.STOPPED)
