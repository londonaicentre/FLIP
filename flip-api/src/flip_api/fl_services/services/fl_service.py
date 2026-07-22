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

import json
import os
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from fastapi import Request
from sqlalchemy import Column
from sqlmodel import Session, col, select

from flip_api.config import get_settings
from flip_api.db.database import get_engine
from flip_api.db.models.main_models import FLJob, Trust
from flip_api.domain.interfaces.fl import (
    DEFAULT_JOB_TYPE,
    IClientStatus,
    IJobMetaData,
    IServerStatus,
    IStartTrainingBody,
    JobRequiredFiles,
)
from flip_api.domain.schemas.status import FLJobStatus, FLTargets, JobStatus
from flip_api.domain.schemas.types import FLBackend
from flip_api.utils.encryption import encrypt
from flip_api.utils.exceptions import JobAbortedError, NotFoundError
from flip_api.utils.http import http_delete, http_get, http_post
from flip_api.utils.logger import logger
from flip_api.utils.s3_client import S3Client


class UnknownJobTypeError(Exception):
    """Custom exception for unknown job types in FL"""

    pass


def list_local_base_files(base_dir: Path) -> list[str]:
    """List every file under a local base-application directory, recursively.

    The base FL application templates live in the repo's ``fl-apps/`` tree, baked into the
    flip-api image and read from ``FL_APP_BASE_DIR`` (FLIP#724) — no longer from S3. This walks
    ``base_dir`` and returns each file's path relative to it, so the bundler can mirror the tree
    1:1 into the destination bucket.

    Args:
        base_dir (Path): Root directory of a backend/job-type base application
            (``<FL_APP_BASE_DIR>/<backend>/<job_type>``).

    Returns:
        list[str]: Sorted relative POSIX paths of every regular file under ``base_dir`` (nested
        paths included). Empty if ``base_dir`` does not exist or contains no files.

    Note:
        Symlinks are ignored — both symlinked files and symlinked directories (``followlinks=False``).
        The default baked-in ``fl-apps/`` tree contains none, but ``FL_APP_BASE_DIR`` may point at an
        operator-provided tree; skipping symlinks keeps the walk inside the template tree so a stray
        link can't pull files from elsewhere on the host into the uploaded bundle.
    """
    if not base_dir.is_dir():
        return []
    rel_paths: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(base_dir, followlinks=False):
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_symlink():
                continue
            rel_paths.append(path.relative_to(base_dir).as_posix())
    return sorted(rel_paths)


def upload_app(model_id: UUID, training_details: IStartTrainingBody, endpoint: str) -> Any:
    """
    Upload the application to the FL server.

    It sends a POST request to the FL API service with the model ID and payload containing the project ID, cohort query,
    local rounds, global rounds, trusts, ignore result error, aggregator, and aggregation weights.

    Args:
        model_id (UUID): The ID of the model to upload.
        training_details (IStartTrainingBody): The payload containing the training details.
        endpoint (str): The endpoint of the net (FL API service).

    Returns:
        Any: The response from the server after uploading the application.
    """
    url = f"{endpoint}/upload_app/{model_id}"
    # Stopgap timeout: staging a large evaluation checkpoint (S3 download -> shared volume on
    # flip-fl-api) can take far longer than httpx's 5s default. Match fl-api-base's staging
    # timeout so this blocking call doesn't abandon an in-progress upload.
    # FIXME: this is a synchronous, blocking call on the run_jobs scheduler path — holding a
    # request open for a multi-hundred-MB (up to 5 GB) transfer is fragile by design. The proper
    # fix is to make upload_app async (flip-fl-api returns 202 + a staging-status endpoint that
    # run_jobs polls, then submit_job once staged) rather than bumping the timeout. Tracked separately.
    response = http_post(url=url, data=training_details.model_dump(), timeout=900)
    logger.info(f"upload_app response: {response}")
    # TODO There should be some response validation here, and the return type should not be Any
    return response


def get_fl_backend_job_id_by_model_id(model_id: UUID, session: Session) -> str:
    """
    Get the FL backend job ID associated with a given model ID

    A re-queued model (STOPPED → INITIATED, #787) keeps its earlier DELETED job rows, so the
    lookup reads only the newest job by ``created`` — the current training attempt (mirrors
    ``update_fl_scheduler``). Deliberately no status filter: ``abort_model_training`` calls this
    right after the dequeue flipped the current job to DELETED, and that job's backend id is
    exactly the one to abort.

    Args:
        model_id (UUID): The ID of the model
        session (Session): SQLModel session object

    Returns:
        str: The FL backend job ID associated with the model ID

    Raises:
        ValueError: If the model has no job, or its newest job was never submitted to the FL
            backend (``fl_backend_job_id`` still NULL).
    """
    statement = (
        select(FLJob.fl_backend_job_id)
        .where(FLJob.model_id == model_id)
        .order_by(cast(Column, FLJob.created).desc())
        .limit(1)
    )
    fl_backend_job_id = session.exec(statement).first()

    if fl_backend_job_id is None:
        raise ValueError(f"No backend job ID found for model_id {model_id}")

    return fl_backend_job_id


def add_fl_backend_job_id(fl_job_id: UUID, fl_backend_job_id: str, session: Session) -> None:
    """
    Add the FL backend job ID to the FLJob entry in the database

    Args:
        fl_job_id (UUID): The ID of the FLJob entry
        fl_backend_job_id (str): The FL backend job ID to add. Needs to be a string as backend job IDs are strings.
        session (Session): SQLModel session object

    Raises:
        ValueError: If the FLJob entry is not found
    """
    fl_job = session.get(FLJob, fl_job_id)
    if fl_job is None:
        raise ValueError(f"FLJob with id {fl_job_id} not found")

    # FL backend job IDs are strings
    fl_job.fl_backend_job_id = fl_backend_job_id
    session.commit()


def submit_job(fl_job_id: UUID, endpoint: str, model_id: UUID, session: Session) -> None:
    """
    Submits a job to the FL API that is going to kick off training

    Args:
        fl_job_id (UUID): The ID of the FL job to add the backend job id given successful job submission
        endpoint (str): The endpoint of the FL API service.
        model_id (UUID): The ID of the model to start submit the job for.
        session (Session): An instance of the database connection.

    Raises:
        ValueError: If the backend job ID is not returned in the response.
    """
    url = f"{endpoint}/submit_job/{model_id}"
    # NOTE we increased the timeout here as Flower submit takes slightly longer to return a response compared to FLARE.
    fl_backend_job_id = http_post(url, timeout=30)
    # Validate that the fl_backend_job_id is returned and is a string
    if not fl_backend_job_id or not isinstance(fl_backend_job_id, str):
        raise ValueError("No backend job id returned or invalid format")
    add_fl_backend_job_id(fl_job_id, fl_backend_job_id, session)


def check_server_status(endpoint: str) -> IServerStatus | None:
    """
    Fetch the status of the server from the FL API.

    Args:
        endpoint (str): The endpoint of the server to check the status from.

    Returns:
        IServerStatus | None: The server status, or ``None`` when the FL API does not respond.
    """
    url = f"{endpoint}/check_server_status"
    logger.debug(f"Checking server status at '{url}'")
    # Same generous timeout as check_client_status: the Flower FL API hits the SuperLink and can
    # be slower than httpx's 5s default.
    response = http_get(url, timeout=30)
    logger.debug(f"Server status response: {response}")
    if not response:
        logger.error(f"No response from FL API for server at endpoint {endpoint}")
        return None
    server_status = IServerStatus.model_validate(response)
    return server_status


def check_client_status(endpoint: str) -> list[IClientStatus] | None:
    """
    Fetch the status of all clients from the FL API.

    Args:
        endpoint (str): The endpoint of the server to check the status from.

    Returns:
        list[IClientStatus] | None: A list of client statuses if available, otherwise None.
    """
    url = f"{endpoint}/check_client_status"
    logger.debug(f"Checking client status at '{url}'")
    # NOTE the Flower FL API queries the SuperLink (ControlServicer.ShowFederation) on each call,
    # which can take ~9s — past httpx's 5s default — so a single check would time out and fail the
    # whole run (run_jobs marks the model ERROR). Use the same generous timeout as Flower submit.
    response = http_get(url, timeout=30)
    logger.debug(f"Client status response: {response}")
    if not response:
        logger.error(f"No response from FL API for clients at endpoint {endpoint}")
        return None
    client_statuses = [IClientStatus.model_validate(c) for c in response]
    return client_statuses


def fetch_server_status(endpoint: str) -> IServerStatus | None:
    """
    Fetch the status of the server from the FL API.

    Args:
        endpoint (str): The endpoint of the server to fetch the status from.

    Returns:
        IServerStatus | None: The server status if available, otherwise None.
    """
    server_status = check_server_status(endpoint)
    if not server_status:
        logger.error(f"No response from FL API for server at endpoint {endpoint}")
        return None
    return server_status


def fetch_client_status(endpoint: str) -> list[IClientStatus] | None:
    """
    Fetch the status of the clients from the FL API.

    Args:
        endpoint (str): The endpoint of the server to fetch the status from.

    Returns:
        list[IClientStatus] | None: A list of client statuses if available, otherwise None.
    """
    client_statuses = check_client_status(endpoint)
    if not client_statuses:
        logger.error(f"No response from FL API for clients at endpoint {endpoint}")
        return None
    return client_statuses


def is_client_available(client_name: str, client_statuses: list[IClientStatus]) -> bool:
    """
    Check if a specific client is available based on its status.

    Args:
        client_name (str): The name of the client to check.
        client_statuses (list[IClientStatus]): A list of client statuses to check against.

    Returns:
        bool: True if the client is available, False otherwise.
    """
    logger.debug(f"Checking availability of client '{client_name}' against statuses: {client_statuses}")
    for client in client_statuses:
        if client.name == client_name:
            return client.online
    # If the client is not found in the statuses, we consider it unavailable
    logger.warning(f"Client {client_name} not found in client statuses")
    return False


def validate_client_availability(clients: list[str], endpoint: str, fl_backend: FLBackend) -> None:
    """
    Validate the availability of clients by checking their status.
    It sends a GET request to the FL API service to check the status of the clients.
    For NVFLARE, raises ValueError if any client is unavailable.
    For Flower, logs a warning instead — Flower's SuperLink handles client selection at runtime.

    Args:
        clients (list[str]): A list of client names to check the availability of.
        endpoint (str): The endpoint of the FL API service.
        fl_backend (FLBackend): The FL backend of the net being validated (``nvflare`` or ``flower``).

    Returns:
        None

    Raises:
        ValueError: If any client is unavailable (NVFLARE backend only).
    """
    is_flower = fl_backend == FLBackend.FLOWER

    client_statuses = check_client_status(endpoint)
    if not client_statuses:
        if is_flower:
            logger.warning(f"No client status response from FL API at {endpoint} — Flower will handle client selection")
            return
        logger.error(f"No response from FL API for clients at endpoint {endpoint}")
        raise ValueError("Unable to fetch client statuses to validate client availability")

    logger.info(f"Client status: {client_statuses}")

    unavailable = [client for client in clients if not is_client_available(client, client_statuses)]

    if unavailable:
        if is_flower:
            logger.warning(f"Clients unavailable: {', '.join(unavailable)} — Flower will handle client selection")
            return
        raise ValueError(f"Clients unavailable: {', '.join(unavailable)}")


def abort_job(endpoint: str, job_id: str) -> dict:
    """
    Aborts a job on the FL server.

    Args:
        endpoint (str): The endpoint of the FL API service.
        job_id (str): The ID of the job to abort.

    Returns:
        dict: The response from the server after aborting the job.
    """
    url = f"{endpoint}/abort_job/{job_id}"
    response = http_delete(url)
    return response


def _raise_if_job_aborted(fl_job_id: UUID, session: Session) -> None:
    """
    Abort gate for the prepare window: raise if the FL job was DELETED mid-prepare.

    Selects the status column (not the entity) so the check bypasses the session identity map
    and sees the latest committed value from a concurrent abort (READ COMMITTED).

    Args:
        fl_job_id (UUID): The ID of the FL job to check.
        session (Session): SQLModel session.

    Raises:
        JobAbortedError: If the job no longer exists or was DELETED by a concurrent abort.
    """
    job_status = session.exec(select(col(FLJob.status)).where(FLJob.id == fl_job_id)).one_or_none()
    if job_status is None or job_status == JobStatus.DELETED:
        raise JobAbortedError(f"FL job {fl_job_id} was aborted before submission")


def start_training(
    model_id: UUID,
    fl_job_id: UUID,
    clients: list[str],
    endpoint: str,
    bundle_urls: list[str],
    session: Session,
) -> None:
    """
    Start the training process for a given model by uploading the application and submitting the job.
    It first checks if the clients are available, then it bundles the application files,
    downloads the configuration, and finally uploads the application and submits the job.

    Args:
        model_id (UUID): The ID of the model to start training for.
        fl_job_id (UUID): The ID of the FL job to add the backend job id given successful job submission.
        clients (list[str]): A list of client names to start training on.
        endpoint (str): The endpoint of the FL API service.
        bundle_urls (list[str]): A list of URLs for the application bundle.
        session (Session): An instance of the database connection.

    Raises:
        ValueError: If the backend job ID is not returned in the response.
    """
    from flip_api.fl_services.services import fl_scheduler_service

    required_info = fl_scheduler_service.get_required_training_details(model_id, session)
    encrypted_project_id = encrypt(required_info.project_id)

    training_details = IStartTrainingBody(
        project_id=encrypted_project_id,
        cohort_query=required_info.cohort_query,
        trusts=clients,
        bundle_urls=bundle_urls,
    )

    # Gate before the (up to 900s) app transfer, and again right before submission: submit_job
    # is the side effect that creates the backend run, so a job aborted mid-prepare (#787) must
    # never reach it.
    _raise_if_job_aborted(fl_job_id, session)
    upload_app(model_id, training_details, endpoint)
    _raise_if_job_aborted(fl_job_id, session)
    logger.info(f"Submitting job for training for model {model_id} with FL job ID {fl_job_id}")
    submit_job(fl_job_id, endpoint, model_id, session)


def bundle_nvflare_application(model_id: UUID, job_type: str = DEFAULT_JOB_TYPE) -> str:
    """
    Creates the app folder from the base application files and the uploaded files.

    It uploads the local base application templates and copies the model files to the destination
    bucket. It checks if the destination bucket has any files, and if it does, it deletes them.

    After copying, path-level verification ensures that all expected files are present in the destination bucket.

    Example:

    Base application files on the local FL_APP_BASE_DIR tree (baked into the image, FLIP#724):

        <FL_APP_BASE_DIR>/nvflare/standard/
        ├── app_site1/
        │   ├── config/
        │   │   └── config_fed_client.json
        │   │   └── config_fed_server.json
        │   └── custom/
        │       └── flip.py [and other files]
        ├── app_site2/
        │   ├── config/
        │   │   └── config_fed_server.json
        │   │   └── config_fed_client.json
        │   └── custom/
        │       └── flip.py [and other files]

    Model files in the model files bucket:

        s3://model-bucket/<model_id>/
        ├── trainer.py
        ├── validator.py
        ├── config.json
        └── [other user uploaded files]

    Final structure in the destination bucket:

        s3://dest-bucket/<model_id>/
        ├── app_site1/
        │   ├── config/
        │   │   └── config_fed_client.json
        │   │   └── config_fed_server.json
        │   ├── custom/
        │   │   ├── [base application files files]
        │   │   ├── trainer.py             ← copied from model files
        │   │   ├── validator.py           ← copied from model files
        │   │   └── config.json            ← copied from model files
        │   │   └── [other user uploaded files]
        ├── app_site2/
        │   ├── config/
        │   │   └── config_fed_server.json
        │   │   └── config_fed_client.json
        │   ├── custom/
        │   │   ├── [base application files]
        │   │   ├── trainer.py             ← copied from model files
        │   │   ├── validator.py           ← copied from model files
        │   │   └── config.json            ← copied from model files
        │   │   └── [other user uploaded files]
        └── meta.json                      ← copied only once (not per app)

    Args:
        model_id (UUID): model ID, which will give the name to the app folder.
        job_type (str, optional): type of job (e.g. 'standard', 'evaluation', etc.). This will cause
        a specific base application to be selected. Defaults to 'standard'.

    Raises:
        EnvironmentError: If the S3 bucket environment variables are not set.
        FileNotFoundError: If the base or model files are missing.
        FileNotFoundError: If required files for the job type are missing.

    Returns:
        str: The destination bucket S3 path where the bundled application is located.
    """
    s3 = S3Client()

    # Construct S3 paths for base, model, and destination buckets
    model_bucket_s3_path = f"{get_settings().SCANNED_MODEL_FILES_BUCKET}/{model_id}"
    dest_bucket_s3_path = f"{get_settings().FL_APP_DESTINATION_BUCKET}/{model_id}"

    logger.debug(f"Model bucket: {model_bucket_s3_path}")
    logger.debug(f"Destination bucket: {dest_bucket_s3_path}")

    # TODO Validate that the buckets are valid S3 paths
    #

    # List objects in the model bucket
    model_files = s3.list_objects(model_bucket_s3_path)
    if not model_files:
        raise FileNotFoundError("Model files missing on the S3 bucket")

    # Determine job_type from config.json if present
    input_config: dict = {}
    config_file = next((k for k in model_files if k.endswith("/config.json")), None)
    if not config_file:
        logger.info("No config.json file was found in the scanned files. Using job_type=standard.")
    else:
        # We download the file
        try:
            s3_config_object = s3.get_object(config_file)
            config_object = s3_config_object["Body"].read()
            input_config = json.loads(config_object) if config_object else {}
        except Exception as e:
            logger.error(f"Error reading config.json from S3: {str(e)}. Using job_type=standard.")
            raise ValueError("Error reading config.json from S3") from e

        jt = input_config.get("job_type")
        if not jt:
            logger.info("No 'job_type' found in config.json. Using job_type=standard.")
        else:
            if not JobRequiredFiles.is_valid_job_type(jt, FLBackend.NVFLARE):
                raise UnknownJobTypeError(f"Unknown job_type argument found in config.json: {jt}")
            job_type = jt
            logger.info(f"job_type in config.json: {job_type}. Using it to select base application.")

    # Locate the base application for this job_type on the local FL_APP_BASE_DIR tree. This
    # bundler is the nvflare-specific path, so the backend segment is fixed:
    # <FL_APP_BASE_DIR>/nvflare/<job_type>. The templates are baked into the flip-api image
    # (FLIP#724) — there is no S3 base bucket.
    base_dir = Path(get_settings().FL_APP_BASE_DIR) / FLBackend.NVFLARE / job_type
    logger.debug(f"Base application dir: {base_dir}")
    base_rel_paths = list_local_base_files(base_dir)
    if not base_rel_paths:
        raise FileNotFoundError(f"Base application files missing in the local base directory: {base_dir}")

    # Clear destination if files already exist there (e.g. from a previous training run)
    dest_files = s3.list_objects(dest_bucket_s3_path)
    if dest_files:
        s3.delete_objects(dest_files)

    # Find app folders (top-level directories that start with "app", e.g. app, app_site1, etc)
    app_folders: set[str] = set()
    for rel in base_rel_paths:
        top = rel.split("/", 1)[0]  # e.g. "app" or "app_site1"
        if top.startswith("app"):
            app_folders.add(top)

    if not app_folders:
        raise FileNotFoundError(f"No app folders found under base application: {base_dir}")

    logger.debug(f"App folders found: {sorted(app_folders)}")

    # Validate required model files exist for the job type before uploading anything, so a bad
    # submission fails fast without leaving a partial bundle in the destination bucket.
    required_files = JobRequiredFiles.get_required_files(job_type, FLBackend.NVFLARE)
    model_rel = {
        k.replace(f"{model_bucket_s3_path}/", "", 1) for k in model_files
    }  # relative paths of model files (i.e. without the bucket prefix)
    missing_files = [f for f in required_files if f not in model_rel]
    if len(missing_files) > 0:
        raise FileNotFoundError(f"Missing required files for job type {job_type}: {', '.join(missing_files)}. ")

    # Upload the base application tree into the destination bucket (1:1 paths under base_dir)
    for rel in base_rel_paths:
        src_path = base_dir / rel
        dest_file_path = f"{dest_bucket_s3_path}/{rel}"
        logger.debug(f"Uploading base {src_path} -> {dest_file_path}")
        s3.upload_file(str(src_path), dest_file_path)

    # Copy meta.json file from model files (if it exists) to the destination bucket
    if f"{model_bucket_s3_path}/meta.json" in model_files:
        src_meta_path = f"{model_bucket_s3_path}/meta.json"
        dest_meta_path = f"{dest_bucket_s3_path}/meta.json"
        logger.debug(f"Copying meta.json {src_meta_path} -> {dest_meta_path}")
        s3.copy_object(src_meta_path, dest_meta_path)

    # Some jobs load a large model checkpoint SERVER-SIDE and don't need it on the clients:
    #   - evaluation jobs: the models[*].checkpoint files, loaded by EvaluationPTModelLocator;
    #   - training jobs: a pretrained backbone declared via top-level SERVER_CHECKPOINT (str or
    #     list), loaded by InitialCheckpointPTModelPersistor and broadcast as the round-0 model.
    # Divert those to a server-only `server_checkpoints/` prefix so they are staged for the
    # fl-server (via the shared checkpoint volume) instead of being bundled into every
    # app*/custom/ and shipped to every client by NVFLARE's deploy_map (a large bundled file
    # collapses app-deploy). Mirrors the Flower backend, which keeps the checkpoint server-side.
    server_checkpoints: set[str] = set()
    if job_type in ("evaluation", "evaluation_client_api"):
        server_checkpoints = {
            m["checkpoint"]
            for m in input_config.get("models", {}).values()
            if isinstance(m, dict) and m.get("checkpoint")
        }
    else:
        declared = input_config.get("SERVER_CHECKPOINT")
        if isinstance(declared, str) and declared:
            server_checkpoints = {declared}
        elif isinstance(declared, list):
            server_checkpoints = {c for c in declared if isinstance(c, str) and c}
    if server_checkpoints:
        logger.info(f"Diverting server-side checkpoints out of the app bundle: {server_checkpoints}")

    # Copy model files, skipping meta.json. Evaluation checkpoints go once to the server-only
    # `server_checkpoints/` prefix; every other model file is mirrored into each app*/custom/.
    for src_key in model_files:
        rel = src_key.replace(f"{model_bucket_s3_path}/", "", 1)

        # Skip meta.json as it is already copied
        if rel == "meta.json":
            continue

        if rel in server_checkpoints:
            # Copied once to a non-app prefix; never placed in app*/custom/, so NVFLARE's
            # deploy_map never ships it to clients. The fl-server reads it off the shared volume.
            dst_key = f"{dest_bucket_s3_path}/server_checkpoints/{rel}"
            logger.debug(f"Copying server-side checkpoint {src_key} -> {dst_key}")
            s3.copy_object(src_key, dst_key)
            continue

        for app in app_folders:
            dst_key = f"{dest_bucket_s3_path}/{app}/custom/{rel}"
            # Check if destination key exists
            if s3.object_exists(dst_key):
                logger.warning(
                    f"The file name {rel} is reserved for this base application, which contains a file with the same "
                    f"name. The researcher can't overwrite it. Skipping upload from model files."
                )
                continue
            logger.debug(f"Copying model file {src_key} -> {dst_key}")
            s3.copy_object(src_key, dst_key)

    # Path-level verification to ensure all expected files are present in the destination bucket after copying
    verify_bundle_paths(
        s3=s3,
        base_rel_paths=base_rel_paths,
        model_files=model_files,
        app_folders=app_folders,
        model_bucket_s3_path=model_bucket_s3_path,
        dest_bucket_s3_path=dest_bucket_s3_path,
        server_checkpoints=server_checkpoints,
    )

    return dest_bucket_s3_path


def bundle_flower_application(model_id: UUID, job_type: str = DEFAULT_JOB_TYPE) -> str:
    """
    Creates the app folder from the base application files and the uploaded files.

    It uploads the local base application templates and copies the model files to the destination
    bucket. It checks if the destination bucket has any files, and if it does, it deletes them.

    Example:

    Base application files on the local FL_APP_BASE_DIR tree (baked into the image, FLIP#724):

        <FL_APP_BASE_DIR>/flower/standard/
        ├── app/
        │   └── server_app.py
        └── pyproject.toml

    Model files in the model files bucket:

        s3://model-bucket/<model_id>/
        ├── client_app.py
        ├── models.py
        ├── config.json
        └── [other user uploaded files]

    Final structure in the destination bucket:

        s3://dest-bucket/<model_id>/
        ├── app/
        │   ├── server_app.py
        │   ├── client_app.py                 ← copied from model files
        │   ├── models.py                     ← copied from model files
        │   ├── config.json                   ← copied from model files
        │   └── [other user uploaded files]
        └── pyproject.toml                    ← copied from base application (not overwritten by model files)

    Args:
        model_id (UUID): model ID, which will give the name to the app folder.
        job_type (str, optional): type of job (e.g. 'standard', 'evaluation', etc.). This will cause
        a specific base application to be selected. Defaults to 'standard'.

    Raises:
        EnvironmentError: If the S3 bucket environment variables are not set.
        FileNotFoundError: If the base or model files are missing.
        FileNotFoundError: If required files for the job type are missing.

    Returns:
        str: The destination bucket S3 path where the bundled application is located.
    """
    s3 = S3Client()

    # Construct S3 paths for base, model, and destination buckets
    model_bucket_s3_path = f"{get_settings().SCANNED_MODEL_FILES_BUCKET}/{model_id}"
    dest_bucket_s3_path = f"{get_settings().FL_APP_DESTINATION_BUCKET}/{model_id}"

    logger.debug(f"Model bucket: {model_bucket_s3_path}")
    logger.debug(f"Destination bucket: {dest_bucket_s3_path}")

    # TODO Validate that the buckets are valid S3 paths
    #

    # List objects in the model bucket
    model_files = s3.list_objects(model_bucket_s3_path)
    if not model_files:
        raise FileNotFoundError("Model files missing on the S3 bucket")

    # Determine job_type from config.json if present
    config_file = next((k for k in model_files if k.endswith("/config.json")), None)
    if not config_file:
        logger.info("No config.json file was found in the scanned files. Using job_type=standard.")
    else:
        # We download the file
        try:
            s3_config_object = s3.get_object(config_file)
            config_object = s3_config_object["Body"].read()
            input_config = json.loads(config_object) if config_object else {}
        except Exception as e:
            logger.error(f"Error reading config.json from S3: {str(e)}. Using job_type=standard.")
            raise ValueError("Error reading config.json from S3") from e

        jt = input_config.get("job_type")
        if not jt:
            logger.info("No 'job_type' found in config.json. Using job_type=standard.")
        else:
            if not JobRequiredFiles.is_valid_job_type(jt, FLBackend.FLOWER):
                raise UnknownJobTypeError(f"Unknown job_type argument found in config.json: {jt}")
            job_type = jt
            logger.info(f"job_type in config.json: {job_type}. Using it to select base application.")

    # Locate the base application for this job_type on the local FL_APP_BASE_DIR tree. This
    # bundler is the flower-specific path, so the backend segment is fixed:
    # <FL_APP_BASE_DIR>/flower/<job_type>. The templates are baked into the flip-api image
    # (FLIP#724) — there is no S3 base bucket.
    base_dir = Path(get_settings().FL_APP_BASE_DIR) / FLBackend.FLOWER / job_type
    logger.debug(f"Base application dir: {base_dir}")
    base_rel_paths = list_local_base_files(base_dir)
    if not base_rel_paths:
        raise FileNotFoundError(f"Base application files missing in the local base directory: {base_dir}")

    # Clear destination if files already exist there (e.g. from a previous training run)
    dest_files = s3.list_objects(dest_bucket_s3_path)
    if dest_files:
        s3.delete_objects(dest_files)

    # Validate required model files exist for the job type before uploading anything, so a bad
    # submission fails fast without leaving a partial bundle in the destination bucket.
    required_files = JobRequiredFiles.get_required_files(job_type, FLBackend.FLOWER)
    model_rel = {
        k.replace(f"{model_bucket_s3_path}/", "", 1) for k in model_files
    }  # relative paths of model files (i.e. without the bucket prefix)
    missing_files = [f for f in required_files if f not in model_rel]
    if len(missing_files) > 0:
        raise FileNotFoundError(f"Missing required files for job type {job_type}: {', '.join(missing_files)}. ")

    # Upload the base application tree into the destination bucket (1:1 paths under base_dir)
    for rel in base_rel_paths:
        src_path = base_dir / rel
        dest_file_path = f"{dest_bucket_s3_path}/{rel}"
        logger.debug(f"Uploading base {src_path} -> {dest_file_path}")
        s3.upload_file(str(src_path), dest_file_path)

    # Copy model files into app/
    for src_key in model_files:
        rel = src_key.replace(f"{model_bucket_s3_path}/", "", 1)

        dst_key = f"{dest_bucket_s3_path}/app/{rel}"
        # Check if destination key exists
        if s3.object_exists(dst_key):
            logger.warning(
                f"The file name {rel} is reserved for this base application, which contains a file with the same "
                f"name. The researcher can't overwrite it. Skipping upload from model files."
            )
            continue
        logger.debug(f"Copying model file {src_key} -> {dst_key}")
        s3.copy_object(src_key, dst_key)

    return dest_bucket_s3_path


def verify_bundle_paths(
    *,
    s3: "S3Client",
    base_rel_paths: list[str],
    model_files: list[str],
    app_folders: set[str],
    model_bucket_s3_path: str,
    dest_bucket_s3_path: str,
    server_checkpoints: set[str] | None = None,
) -> None:
    """
    Verifies that all expected destination keys exist after bundling.

    Args:
        s3 (S3Client): S3 client used to list destination objects.
        base_rel_paths (list[str]): Relative paths of the base application files (relative to the
            local base directory), each mirrored 1:1 into the destination bundle.
        model_files (list[str]): Keys of the user-uploaded model files in the source bucket.
        app_folders (set[str]): Application subfolder names that model files get mirrored into.
        model_bucket_s3_path (str): Root S3 path of the user model bucket.
        dest_bucket_s3_path (str): Root S3 path of the destination bundle bucket.
        server_checkpoints (set[str] | None): Model-file names diverted to the server-only
            ``server_checkpoints/`` prefix (evaluation jobs); expected there instead of in
            each ``app*/custom/``.

    Raises:
        RuntimeError: If any expected destination key is missing from the bundle bucket.
    """

    # Relative paths of model files
    model_rel = {k.replace(f"{model_bucket_s3_path}/", "", 1) for k in model_files}

    # Construct the set of expected destination keys based on the base files, model files, and app folders
    expected: set[str] = set()

    # Base files (mirrored exactly)
    for rel in base_rel_paths:
        expected.add(f"{dest_bucket_s3_path}/{rel}")

    # meta.json copied once
    if "meta.json" in model_rel:
        expected.add(f"{dest_bucket_s3_path}/meta.json")

    # Model files copied into each app/custom (skip meta.json). Evaluation checkpoints are
    # diverted once to the server-only `server_checkpoints/` prefix instead of app*/custom/.
    server_checkpoints = server_checkpoints or set()
    for rel in model_rel:
        if rel == "meta.json":
            continue
        if rel in server_checkpoints:
            expected.add(f"{dest_bucket_s3_path}/server_checkpoints/{rel}")
            continue
        for app in app_folders:
            expected.add(f"{dest_bucket_s3_path}/{app}/custom/{rel}")

    # List actual destination keys
    actual = set(s3.list_objects(dest_bucket_s3_path))

    # Check for missing files
    missing = expected - actual
    if missing:
        raise RuntimeError(
            f"Bundle verification failed: {len(missing)} missing files. Examples: {sorted(missing)[:10]}"
        )

    logger.info(f"Bundle verification succeeded: {len(expected)} files present.")


def get_bundle_urls(s3_path: str) -> list[str]:
    """
    Creates pre-signed URLs for the bundle files in S3 (containing the application files and model files) that the FL
    API will use for training.

    Args:
        s3_path (str): The S3 path of the bundle to get the URLs for.

    Returns:
        list[str]: A list of pre-signed URLs for the bundle files.

    Raises:
        ClientError: If there is an error listing objects or generating pre-signed URLs.
    """
    logger.info(f"Getting bundle URLs from {s3_path}")

    s3 = S3Client()

    try:
        # List objects in the destination S3 bucket
        files = s3.list_objects(s3_path)
    except Exception as e:
        error_msg = f"Failed to list objects in S3 bucket {s3_path}: {e}"
        logger.error(error_msg)
        raise RuntimeError(error_msg)

    # Generate presigned URLs for each object to be downloaded
    try:
        urls = [s3.get_presigned_url(f) for f in files]
        return urls
    except Exception as e:
        error_msg = f"Failed to generate presigned URLs: {e}"
        logger.error(error_msg)
        raise RuntimeError(error_msg)


def extract_current_job_data(net_endpoint: str, fl_backend_job_id: str) -> IJobMetaData | None:
    """
    Extract the currently-running FL job matching ``fl_backend_job_id``.

    Args:
        net_endpoint (str): The endpoint of the FL API service.
        fl_backend_job_id (str): The FL job ID to look for.

    Returns:
        IJobMetaData | None: The running job's metadata, or ``None`` if no running job
            matches ``fl_backend_job_id`` (the job is already terminal or never started).

    Raises:
        ValueError: If the FL server response is not a list, or more than one running
            job shares the same ID.
        pydantic.ValidationError: If a returned item does not conform to ``IJobMetaData``
            (e.g. an unknown status from a non-conforming FL-API adapter) — failing loudly
            here is intentional.
    """
    url = f"{net_endpoint}/list_jobs"
    # Same generous timeout as the other FL status checks: the Flower FL API lists runs from the
    # SuperLink and can exceed httpx's 5s default; this is polled during a run, so a 5s timeout
    # would risk flipping a healthy in-flight model to ERROR.
    current_job_data = http_get(url, timeout=30)
    logger.debug(f"Current job data: {current_job_data}")

    # Validate the response format
    if not isinstance(current_job_data, list):
        error_msg = f"Unexpected response format from {url}: {current_job_data}"
        logger.error(error_msg)
        raise ValueError(error_msg)

    current_job_data = [IJobMetaData.model_validate(j) for j in current_job_data]

    # Get the running jobs only
    current_job_data = [j for j in current_job_data if j.status == FLJobStatus.RUNNING]
    logger.debug(f"Running jobs: {current_job_data}")

    # Filter the fl_backend_job_id
    current_job_data = [j for j in current_job_data if j.job_id == fl_backend_job_id]
    logger.debug(f"Current job data for job ID {fl_backend_job_id}: {current_job_data}")

    if not current_job_data:
        logger.info(
            f"No running job with ID {fl_backend_job_id} on FL server {net_endpoint}; "
            f"it is already terminal or never started."
        )
        return None

    # assert that there is only 1 running job with the fl_backend_job_id
    # this should not happen, but just in case
    if len(current_job_data) > 1:
        error_msg = f"Multiple running jobs found on FL server for job ID {fl_backend_job_id}. Cannot abort."
        logger.error(error_msg)
        raise ValueError(error_msg)

    return current_job_data[0]


def abort_model_training(request: Request, model_id: UUID, session: Session) -> None:
    """
    Check if the model is currently running training, and if it is, send an abort request to the FL server.

    Args:
        request (Request): The FastAPI request object
        model_id (UUID): The ID of the model to abort
        session (Session): SQLModel session object

    Raises:
        ValueError: If the FL server is not running, or if ``target`` is invalid.
        DatabaseError: If the dequeue or a scheduler lookup fails at the DB layer — surfaced
            rather than swallowed, so a failed abort is never reported as a success.
    """
    logger.debug(f"Checking if model {model_id} is currently running...")

    # Imported locally to avoid the fl_service -> model_service -> fl_scheduler_service cycle.
    from flip_api.fl_services.services import fl_scheduler_service
    from flip_api.model_services.services.model_service import add_log

    try:
        # Always try to remove the job from queue
        fl_scheduler_service.remove_job_from_queue(model_id, session)

        fl_backend_job_id = get_fl_backend_job_id_by_model_id(model_id, session)
        net_details = fl_scheduler_service.get_net_by_model_id(model_id, session)
        net_endpoint = net_details.endpoint
        net_name = net_details.name

        logger.info(f"Net info for model {model_id}: endpoint={net_endpoint}, name={net_name}")

    except (NotFoundError, ValueError) as e:
        # Pre-running window (#787): the job was dequeued but was never submitted to the
        # fl-server (fl_backend_job_id still NULL → ValueError) or no net is pinned to it yet
        # (NotFoundError), so there is nothing to abort — but the net may already be BUSY with
        # this job's pickup. Release it now rather than leaving it to the stale-BUSY watchdog on
        # the next scheduler tick. Deliberately narrow: a DatabaseError here means the dequeue
        # or lookup itself failed, and must surface as an error — not as a successful abort.
        released = fl_scheduler_service.release_scheduler_for_model(model_id, session)
        logger.info(
            f"Model {model_id} not currently running training; removed from queue "
            f"(released {released} scheduler(s)). Reason: {e}"
        )
        add_log(model_id, "Training job aborted before start; training slot released.", session)
        return

    server_status = fetch_server_status(net_endpoint)
    logger.debug(f"Server status: {server_status}")

    if not server_status:  # or server_status.status != FLStatus.SUCCESS.value:
        error_msg = f"FL Server not running for {model_id=}. Server status: {server_status}"
        logger.error(error_msg)
        raise ValueError(error_msg)

    # If there is no running job for this model, it is already terminal — abort is an
    # idempotent no-op. The jobs were just dequeued above, so free the net promptly instead of
    # leaving it to the stale-BUSY watchdog.
    if extract_current_job_data(net_endpoint, fl_backend_job_id) is None:
        fl_scheduler_service.release_scheduler_for_model(model_id, session)
        logger.info(
            f"No running FL job for model {model_id} (job ID {fl_backend_job_id}); "
            f"already stopped — nothing to abort."
        )
        return

    # Extracting target and clients from the request path parameters
    path_params = request.path_params
    target = path_params.get("target")
    clients = path_params.get("clients")

    # Checking if the target provided is valid
    if target and target not in FLTargets:
        logger.error(f"Invalid target: {target}")
        raise ValueError(f"Invalid target: {target}")

    logger.debug(f"Attempting abort request for model ID: {model_id} on {net_name} (job ID: {fl_backend_job_id})")

    response = abort_job(net_endpoint, fl_backend_job_id)

    logger.info(f"Abort job response ({target=}, {clients=}): {response}")

    # The dequeue above DELETEd the model's jobs, so update_fl_scheduler (which only considers
    # non-DELETED jobs) can no longer free the net — release it here now the abort is delivered.
    released = fl_scheduler_service.release_scheduler_for_model(model_id, session)
    logger.info(f"Released {released} scheduler(s) for model {model_id} after abort")


def add_fl_job(model_id: UUID, trusts: list[Trust], session: Session) -> None:
    """
    Insert a new FL job into the database with its trust participants.

    Args:
        model_id (UUID): The ID of the model for which the FL job is being created.
        trusts (list[Trust]): Trust rows participating in this job. Stored via the
            `fl_job_trust` link table — the relationship gives `job.trusts` direct
            access to full Trust ORM rows without a manual id-to-name lookup.
        session (Session): The SQLModel session to use for the database operation.

    Raises:
        Exception: If there is an error during the database operation.
    """
    logger.debug(f"Adding FL job for model ID: {model_id}")

    job = FLJob(model_id=model_id, trusts=trusts)

    try:
        session.add(job)
        session.commit()
        session.refresh(job)  # Pull back generated fields like created timestamp
    except Exception as e:
        session.rollback()
        logger.error(f"Failed to insert FL job for model ID {model_id}: {e}")
        raise

    logger.info(f"FL job {job.id} added for model ID: {model_id}")


def keep_fl_api_session_alive() -> None:
    """
    A periodic function to keep the FL API session alive by making a simple request.
    This is useful to prevent the session from going idle or being shut down by the server.

    TODO This was developed for the NVFLARE backend and might need to be revisited for the Flower backend.
    See https://github.com/NVIDIA/NVFlare/discussions/3526#discussioncomment-13574644
    """
    from flip_api.fl_services.get_status import fetch_server_status
    from flip_api.fl_services.services import fl_scheduler_service

    logger.info("🛟 Keeping FL API session alive ...")

    # For each FL Net in the database, call its check_server_status endpoint to keep the session alive.
    # NOTE this was created for FLARE and might need to be revisited for Flower, depending on session management.
    # NOTE In the old implementation, we had 3 'nets' in the database, each with its own FLAdminAPI. So each net had a
    # separate FLAdminAPI endpoint. Here, there should just be 1 net for now. If we add more nets in the future, they
    # might all have the same FLARE_API endpoint, if the FLARE_API controls all controllers/clients.
    with Session(get_engine()) as db:
        nets = fl_scheduler_service.get_nets(db)

        for net in nets:
            try:
                fetch_server_status(net.endpoint)
            except Exception as e:
                logger.error(f"Failed to send check request: {e}")
