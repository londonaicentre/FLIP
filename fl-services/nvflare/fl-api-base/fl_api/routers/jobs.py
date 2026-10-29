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
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from fl_api.core.dependencies import get_studies
from fl_api.utils.schemas import JobMetadata, JobStatus, TargetType, normalize_status
from fl_api.utils.study_sessions import StudySessions
from fl_api.utils.upload import job_sites

router = APIRouter()


@router.post("/submit_job/{job_folder}")
def submit_job(job_folder: UUID, study: str | None = None, studies: StudySessions = Depends(get_studies)) -> str:
    """
    Submits an existing job to the server.

    Args:
        job_folder (UUID): The Central Hub model_id. The uploaded job lives in the folder
            named after it (created by /upload_app/{model_id}), so the submit "job folder"
            and the upload "model_id" are the same UUID; flip-api is the only caller.
            FastAPI rejects any non-UUID path segment with 422 before this handler runs.
        study (str | None): The model's project id. The job is registered in that NVFLARE study, with the sites it
            was uploaded for, and submitted from a session in it (FLIP#1678). None submits to the default study.
        studies (StudySessions): The FLARE sessions, per study.

    Returns:
        str: job ID if the system accepts the job.

    Raises:
        HTTPException: if the job submission fails due to any reason.
    """
    if study:
        studies.register(study, job_sites(studies.default.upload_dir, str(job_folder)))
    return studies.get(study).submit_job(str(job_folder))


@router.get("/download_job/{job_id}")
def download_job(job_id: str, study: str | None = None, studies: StudySessions = Depends(get_studies)) -> str:
    """
    Downloads the job result of the specified job ID.

    Args:
        job_id (str): job ID to be downloaded.
        study (str | None): The NVFLARE study the job runs in (its project id), or None for the default.
        studies (StudySessions): The FLARE sessions, per study.

    Returns:
        str: location of the downloaded job result.
    """
    return studies.get(study).download_job_result(job_id)


@router.get("/list_jobs", response_model=list[JobMetadata])
def list_jobs(
    detailed: bool = False,
    limit: int | None = None,
    id_prefix: str | None = None,
    name_prefix: str | None = None,
    reverse: bool = False,
    study: list[str] = Query(default=[]),
    studies: StudySessions = Depends(get_studies),
) -> list[JobMetadata]:
    """
    Returns a list of available jobs on the server, as the shared job-metadata contract.

    Args:
        detailed (bool, optional): whether extensive description is demanded. Defaults to False.
        limit (int | None, optional): maximum number of jobs to display. Defaults to None.
        id_prefix (str | None, optional): prefix for job ID search. Defaults to None.
        name_prefix (str | None, optional): prefix for the job NAME search. Defaults to None.
        reverse (bool, optional): if True, the order will be the reverse of submission time (otherwise it's the
        opposite). Defaults to False.
        study (str | None): The NVFLARE study the job runs in (its project id), or None for the default.
        studies (StudySessions): The FLARE sessions, per study.

    Returns:
        list[JobMetadata]: the available jobs as normalized job-metadata contract items.

    Raises:
        HTTPException: if an error occurs while listing jobs.
    """
    # NVFLARE lists a session's own study only (FLIP#1678): the default study, which holds jobs submitted before
    # studies, and every study the caller names.
    raw_jobs: list[dict] = []
    for name in [None, *dict.fromkeys(study)]:
        raw_jobs.extend(
            studies.list_jobs(
                name, detailed=detailed, limit=limit, id_prefix=id_prefix, name_prefix=name_prefix, reverse=reverse
            )
        )
    if limit:
        raw_jobs = raw_jobs[:limit]
    # FLARE's list_jobs always includes job_id + status on every entry (verified against
    # nvflare 2.7.1 job_cmds.py, both the summary and detailed paths) — bracket access is intentional.
    return [JobMetadata(job_id=str(job["job_id"]), status=normalize_status(job["status"])) for job in raw_jobs]


@router.post("/{job_id}/show_errors/{target_type}")
@router.post("/{job_id}/show_errors/{target_type}/{targets}")
def show_errors(
    job_id: str,
    target_type: TargetType,
    targets: str | None = None,
    study: str | None = None,
    studies: StudySessions = Depends(get_studies),
) -> dict:
    """
    Show processing errors of specified job on specified targets.

    Args:
        job_id (str): ID of the job
        target_type (TargetType): type of target (server or client)
        targets (str | None): comma-separated client names if target type is "client". All clients if not
            specified.
        study (str | None): The NVFLARE study the job runs in (its project id), or None for the default.
        studies (StudySessions): The FLARE sessions, per study.

    Returns:
        dict: job errors (if any) on specified targets. The key of the dict is target name. The value is a dict of
        errors reported by different system components (ServerRunner or ClientRunner).
    """
    return studies.get(study).show_errors(job_id, target_type, (targets.split(",") if targets else None))


@router.post("/show_stats/{target_type}/{job_id}")
@router.post("/show_stats/{target_type}/{targets}/{job_id}")
def show_stats(
    job_id: str,
    target_type: TargetType,
    targets: str | None = None,
    study: str | None = None,
    studies: StudySessions = Depends(get_studies),
) -> dict:
    """
    Show processing stats of specified job on specified targets.

    Args:
        job_id (str): ID of the job
        target_type (TargetType): type of target (server or client)
        targets (str | None): comma-separated client names if target type is "client". All clients if not
            specified.
        study (str | None): The NVFLARE study the job runs in (its project id), or None for the default.
        studies (StudySessions): The FLARE sessions, per study.

    Returns:
        dict: job stats on specified targets. The key of the dict is target name. The value is a dict of stats reported
        by different system components (ServerRunner or ClientRunner).

    Raises:
        HTTPException: if an error occurs while showing stats for the job.
    """
    return studies.get(study).show_stats(job_id, target_type, (targets.split(",") if targets else None))


@router.post("/reset_errors", status_code=status.HTTP_200_OK)
def reset_errors(
    job_id: str, study: str | None = None, studies: StudySessions = Depends(get_studies)
) -> dict[str, str]:
    """
    Resets the errors of a specific job.

    Args:
        job_id (str): job ID.
        study (str | None): The NVFLARE study the job runs in (its project id), or None for the default.
        studies (StudySessions): The FLARE sessions, per study.

    Returns:
        dict[str, str]: a dictionary containing the status and information about the error reset operation.

    Raises:
        HTTPException: if the job is not found or if an error occurs during the reset process.
    """
    studies.get(study).reset_errors(job_id)
    return {"status": "success", "info": f"Errors for job {job_id} have been reset."}


@router.delete("/delete_job/{job_id}", status_code=status.HTTP_200_OK)
def delete_job(job_id: str, study: str | None = None, studies: StudySessions = Depends(get_studies)) -> dict[str, str]:
    """
    Deletes a job from the server if it's not running.

    Args:
        job_id (str): job ID to be deleted.
        study (str | None): The NVFLARE study the job runs in (its project id), or None for the default.
        studies (StudySessions): The FLARE sessions, per study.

    Raises:
        HTTPException: if the job ID does not exist or if an error occurs during the deletion process.

    Returns:
        dict[str, str]: a dictionary containing the status and information about the job deletion operation.
    """
    studies.get(study).delete_job(job_id)
    return {"status": "success", "info": f"Job {job_id} deleted."}


@router.delete("/abort_job/{job_id}", status_code=status.HTTP_200_OK, response_model=JobMetadata)
def abort_job(job_id: str, study: str | None = None, studies: StudySessions = Depends(get_studies)) -> JobMetadata:
    """Aborts job with provided job_id.

    FLARE's ``abort_job`` is natively idempotent for already-terminal jobs ("if the job
    is already done, no effect"); a genuinely nonexistent ``job_id`` raises ``JobNotFound``,
    handled as a 404 by the app-level exception handler.

    Args:
        job_id (str): job ID.
        study (str | None): The NVFLARE study the job runs in (its project id), or None for the default.
        studies (StudySessions): The FLARE sessions, per study.

    Raises:
        HTTPException: 404 if the job id does not exist.

    Returns:
        JobMetadata: the aborted job as a normalized job-metadata contract item.
    """
    studies.get(study).abort_job(job_id)
    return JobMetadata(job_id=job_id, status=JobStatus.STOPPED)
