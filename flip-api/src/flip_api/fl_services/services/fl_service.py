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
from typing import Any, List, Optional
from urllib.parse import urlparse
from uuid import UUID

from fastapi import Request
from sqlmodel import Session, select

from flip_api.config import get_settings
from flip_api.db.database import engine
from flip_api.db.models.main_models import FLJob
from flip_api.domain.interfaces.fl import (
    AggregationWeights,
    FLAggregators,
    IClientStatus,
    IJobMetaData,
    IOverridableConfig,
    IServerStatus,
    IStartTrainingBody,
    JobRequiredFiles,
    JobTypes,
)
from flip_api.domain.interfaces.shared import TrainingRound
from flip_api.domain.schemas.fl import ClientInfoModel
from flip_api.domain.schemas.status import ClientStatus, FLTargets
from flip_api.model_services.services.model_service import add_log
from flip_api.utils.encryption import encrypt
from flip_api.utils.http import http_delete, http_get, http_post
from flip_api.utils.logger import logger
from flip_api.utils.s3_client import S3Client


class UnknownJobTypeError(Exception):
    """Custom exception for unknown job types in FL"""

    pass


def validate_config(config: IOverridableConfig) -> IOverridableConfig:
    """
    Validate the provided configuration dictionary.

    Args:
        config (IOverridableConfig): The configuration dictionary to validate.

    Returns:
        IOverridableConfig: The validated configuration dictionary.

    Raises:
        ValueError: If any of the checks fail, a ValueError is raised with an appropriate message.
    """
    validated = IOverridableConfig()

    def is_valid(value):
        return isinstance(value, (int, float)) and 0 < value <= 100

    if not isinstance(config, dict):
        raise ValueError("Provided config is not a valid dictionary")

    if is_valid(config.get("LOCAL_ROUNDS")):
        validated.LOCAL_ROUNDS = config["LOCAL_ROUNDS"]

    if is_valid(config.get("GLOBAL_ROUNDS")):
        validated.GLOBAL_ROUNDS = config["GLOBAL_ROUNDS"]

    if isinstance(config.get("IGNORE_RESULT_ERROR"), bool):
        validated.IGNORE_RESULT_ERROR = config["IGNORE_RESULT_ERROR"]

    agg = config.get("AGGREGATOR")
    if agg:
        if agg in FLAggregators:
            validated.AGGREGATOR = agg
        else:
            raise ValueError(f"Unknown aggregator: {agg}")

    weights = config.get("AGGREGATION_WEIGHTS")
    if weights:
        if not isinstance(weights, dict):
            raise ValueError("AGGREGATION_WEIGHTS must be a dictionary")

        for key, val in weights.items():
            logger.info(f"Validating aggregation weight: {key} -> {val}")
            if not (
                isinstance(val, (int, float))
                and AggregationWeights.MinimumAggregationWeight <= val <= AggregationWeights.MaximumAggregationWeight
            ):
                raise ValueError(f"Invalid weight: {val}")

        validated.AGGREGATION_WEIGHTS = weights

    return validated


def download_config(bundle_urls: List[str], model_id: UUID) -> Optional[IOverridableConfig]:
    """
    Download the config.json file from the bundle URLs and validate its contents.
    If the file is found and valid, return the validated config.
    If the file is not found or invalid, return None.

    Args:
        bundle_urls (List[str]): A list of URLs to download the config.json file from.
        model_id (UUID): The ID of the model.

    Returns:
        Optional[IOverridableConfig]: The validated config if found, otherwise None.
    """
    for url in bundle_urls:
        if "/custom/config.json" in urlparse(url).path:
            config = http_get(url)
            return validate_config(config)
    logger.info("No config.json file found.")
    return None


def upload_app(model_id: UUID, body: IStartTrainingBody, request_id: str, endpoint: str) -> Any:
    """
    Upload the application to the FL server.

    It sends a POST request to the FL API service with the model ID and payload containing the project ID, cohort query,
    local rounds, global rounds, trusts, ignore result error, aggregator, and aggregation weights.

    Args:
        model_id (UUID): The ID of the model to upload.
        body (IStartTrainingBody): The payload containing the training details.
        request_id (str): The request ID of the request that triggered the function.
        endpoint (str): The endpoint of the net (FL API service).

    Returns:
        Any: The response from the server after uploading the application.
    """
    url = f"{endpoint}/upload_app/{model_id}"
    response = http_post(url, request_id, body.model_dump())
    logger.info(f"upload_app response: {response}")
    # TODO There should be some response validation here, and the return type should not be Any
    return response


def get_fl_backend_job_id_by_model_id(model_id: UUID, session: Session) -> str:
    """
    Get the FL backend job ID associated with a given model ID

    Args:
        model_id (UUID): The ID of the model
        session (Session): SQLModel session object

    Returns:
        str: The FL backend job ID associated with the model ID

    Raises:
        ValueError: If the model ID is not found in the database
    """
    statement = select(FLJob.fl_backend_job_id).where(FLJob.model_id == model_id)
    result = session.exec(statement)
    fl_backend_job_id = result.one_or_none()

    if fl_backend_job_id is None:
        raise ValueError(f"No backend job ID found for model_id {model_id}")

    return fl_backend_job_id


def add_fl_backend_job_id(fl_job_id: UUID, fl_backend_job_id: str, session: Session):
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


def submit_job(request_id: str, fl_job_id: UUID, endpoint: str, model_id: UUID, session: Session):
    """
    Submits a job to the FL API that is going to kick off training

    Args:
        request_id (str): The request ID of the request that triggered the function.
        fl_job_id (UUID): The ID of the FL job to add the backend job id given successful job submission
        endpoint (str): The endpoint of the Flare Loader service.
        model_id (UUID): The ID of the model to start submit the job for.
        session (Session): An instance of the database connection.

    Raises:
        ValueError: If the backend job ID is not returned in the response.
    """
    url = f"{endpoint}/submit_job/{model_id}"
    fl_backend_job_id = http_post(url, request_id)
    # Validate that the fl_backend_job_id is returned and is a string
    if not fl_backend_job_id or not isinstance(fl_backend_job_id, str):
        raise ValueError("No backend job id returned or invalid format")
    add_fl_backend_job_id(fl_job_id, fl_backend_job_id, session)


def check_server_status(request_id: str, endpoint: str) -> IServerStatus | None:
    """
    Fetch the status of the server from the FL API.

    Args:
        request_id (str): The request ID for logging purposes.
        endpoint (str): The endpoint of the server to check the status from.

    Returns:
        IServerStatus: The server status.
    """
    url = f"{endpoint}/check_server_status"
    logger.debug(f"Checking server status at '{url}' with request_id '{request_id}'")
    response = http_get(url, request_id)
    logger.debug(f"Server status response: {response}")
    if not response:
        logger.error(f"No response from FL API for server at endpoint {endpoint}")
        return None
    server_status = IServerStatus.model_validate(response)
    return server_status


def check_client_status(request_id: str, endpoint: str) -> List[ClientInfoModel] | None:
    """
    Fetch the status of all clients from the FL API.

    Args:
        request_id (str): The request ID for logging purposes.
        endpoint (str): The endpoint of the server to check the status from.

    Returns:
        List[ClientInfoModel] | None: A list of client statuses if available, otherwise None.
    """
    url = f"{endpoint}/check_client_status"
    logger.debug(f"Checking client status at '{url}' with request_id '{request_id}'")
    response = http_get(url, request_id)
    logger.debug(f"Client status response: {response}")
    if not response:
        logger.error(f"No response from FL API for clients at endpoint {endpoint}")
        return None
    client_statuses = [ClientInfoModel.model_validate(c) for c in response]
    return client_statuses


def fetch_server_status(request_id: str, endpoint: str) -> IServerStatus | None:
    """
    Fetch the status of the server from the FL API.

    Args:
        request_id (str): The request ID for logging purposes.
        endpoint (str): The endpoint of the server to fetch the status from.

    Returns:
        IServerStatus | None: The server status if available, otherwise None.
    """
    server_status = check_server_status(request_id, endpoint)
    if not server_status:
        logger.error(f"No response from FL API for server at endpoint {endpoint}")
        return None
    return server_status


def fetch_client_status(request_id: str, endpoint: str) -> List[IClientStatus] | None:
    """
    Fetch the status of the clients from the FL API.

    Args:
        request_id (str): The request ID for logging purposes.
        endpoint (str): The endpoint of the server to fetch the status from.

    Returns:
        List[IClientStatus] | None: A list of client statuses if available, otherwise None.
    """
    client_statuses = check_client_status(request_id, endpoint)
    if not client_statuses:
        logger.error(f"No response from FL API for clients at endpoint {endpoint}")
        return None

    # Convert ClientInfoModel to IClientStatus and determine online status based on the status field
    # TODO Merge ClientInfoModel and IClientStatus into a single model to avoid redundant parsing and validation
    clients = []
    for client in client_statuses:
        is_online = client.status != ClientStatus.NO_REPLY
        clients.append(
            IClientStatus(
                name=client.name,
                online=is_online,
                status=client.status,
                last_connected=client.last_connect_time,
            )
        )

    return clients


def validate_client_availability(clients: List[str], endpoint: str, request_id: str) -> None:
    """
    Validate the availability of clients by checking their status.
    It sends a GET request to the Flare Loader service to check the status of the clients.
    If any client is unavailable, it raises a ValueError.

    Args:
        clients (List[str]): A list of client names to check the availability of.
        endpoint (str): The endpoint of the Flare Loader service.
        request_id (str): The request ID of the request that triggered the function.

    Returns:
        None

    Raises:
        ValueError: If any client is unavailable.
    """
    client_statuses = check_client_status(request_id, endpoint)
    if not client_statuses:
        logger.error(f"No response from FL API for clients at endpoint {endpoint}")
        raise ValueError("Unable to fetch client statuses to validate client availability")

    logger.info(f"Client status: {client_statuses}")

    def is_client_available(client_name: str, client_statuses: List[ClientInfoModel]) -> bool:
        """Check if a specific client is available based on its status."""
        for status in client_statuses:
            logger.info(f"Checking client status: {status}")
            logger.info(f"Client name: {client_name}")
            name = status.name
            state = status.status
            if name == client_name and state != ClientStatus.NO_REPLY:
                return True
        return False

    unavailable = [client for client in clients if not is_client_available(client, client_statuses)]

    if unavailable:
        raise ValueError(f"Clients unavailable: {', '.join(unavailable)}")


def abort_job(request_id: str, endpoint: str, job_id: str) -> dict:
    """
    Aborts a job on the FL server.

    Args:
        request_id (str): The request ID of the request that triggered the function.
        endpoint (str): The endpoint of the Flare Loader service.
        job_id (str): The ID of the job to abort.

    Returns:
        dict: The response from the server after aborting the job.
    """
    url = f"{endpoint}/abort_job/{job_id}"
    response = http_delete(url, request_id)
    return response


def start_training(
    model_id: UUID,
    fl_job_id: UUID,
    clients: List[str],
    endpoint: str,
    bundle_urls: List[str],
    request_id: str,
    session: Session,
    job_type: JobTypes = JobTypes.standard,  # type: ignore[attr-defined]
):
    """
    Start the training process for a given model by uploading the application and submitting the job.
    It first checks if the clients are available, then it bundles the application files,
    downloads the configuration, and finally uploads the application and submits the job.

    Args:
        model_id (UUID): The ID of the model to start training for.
        fl_job_id (UUID): The ID of the FL job to add the backend job id given successful job submission.
        clients (List[str]): A list of client names to start training on.
        endpoint (str): The endpoint of the Flare Loader service.
        bundle_urls (List[str]): A list of URLs for the application bundle.
        request_id (str): The request ID of the request that triggered the function.
        session (Session): An instance of the database connection.
        job_type (JobTypes): The type of job (e.g., 'standard', 'evaluation'). Defaults to 'standard'.

    Raises:
        ValueError: If the backend job ID is not returned in the response.
    """
    from flip_api.fl_services.services import fl_scheduler_service

    required_info = fl_scheduler_service.get_required_training_details(model_id, session)
    encrypted_project_id = encrypt(required_info.project_id)

    config = download_config(bundle_urls, model_id)
    if config:
        local_rounds = config.LOCAL_ROUNDS if config.LOCAL_ROUNDS else TrainingRound.MIN
        global_rounds = config.GLOBAL_ROUNDS if config.GLOBAL_ROUNDS else TrainingRound.MIN
        aggregator = config.AGGREGATOR if config.AGGREGATOR else FLAggregators.InTimeAccumulateWeightedAggregator.value
        aggregation_weights = config.AGGREGATION_WEIGHTS if config.AGGREGATION_WEIGHTS else {}
        ignore_result_error = config.IGNORE_RESULT_ERROR if config.IGNORE_RESULT_ERROR else False
    else:
        local_rounds = TrainingRound.MIN
        global_rounds = TrainingRound.MIN
        aggregator = FLAggregators.InTimeAccumulateWeightedAggregator.value
        aggregation_weights = {}
        ignore_result_error = False

    body = IStartTrainingBody(
        project_id=encrypted_project_id,
        cohort_query=required_info.cohort_query,
        local_rounds=local_rounds,
        global_rounds=global_rounds,
        trusts=clients,
        aggregator=aggregator,
        aggregation_weights=aggregation_weights,
        bundle_urls=bundle_urls,
        ignore_result_error=ignore_result_error,
    )

    upload_app(model_id, body, request_id, endpoint)
    logger.info(f"Submitting job for training for model {model_id} with FL job ID {fl_job_id}")
    submit_job(request_id, fl_job_id, endpoint, model_id, session)

    if config:
        add_log(model_id, "Config file found ✅", session)
        if not config.AGGREGATION_WEIGHTS:
            add_log(model_id, "No aggregation weights provided. Using default = 1", session)
        else:
            weight_summary = ", ".join([f"{k}: {v}" for k, v in config.AGGREGATION_WEIGHTS.items()])
            add_log(model_id, f"Aggregation weights for training: {weight_summary}", session)


def bundle_application(model_id: UUID, job_type: JobTypes = JobTypes.standard) -> JobTypes:  # type: ignore[attr-defined]
    """
    Creates the app folder from the base application files and the uploaded files.

    It copies the base application files and the model files to the destination bucket.
    It checks if the destination bucket has any files, and if it does, it deletes them.

    After copying, path-level verification ensures that all expected files are present in the destination bucket.

    Example:

    Base application files in the base bucket:

        s3://base-bucket/src/standard/
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
        job_type (JobTypes, optional): type of job (e.g. 'standard', 'evaluation', etc.). This will cause
        a specific base application to be selected. Defaults to 'standard'.

    Raises:
        EnvironmentError: If the S3 bucket environment variables are not set.
        FileNotFoundError: If the base or model files are missing.
        FileNotFoundError: If required files for the job type are missing.

    Returns:
        JobTypes: the job type used for the application (e.g. 'standard', 'evaluation', etc.)
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
        s3_config_object = s3.get_object(config_file)
        config_object = s3_config_object["Body"].read()
        input_config = json.loads(config_object) if config_object else {}

        jt = input_config.get("job_type")
        if not jt:
            logger.info("No 'job_type' found in config.json. Using job_type=standard.")
        else:
            try:
                job_type = JobTypes(jt)
            except ValueError:
                raise UnknownJobTypeError(f"Unknown job_type argument found in config.json: {jt}")
            logger.info(f"job_type in config.json: {job_type.value}. Using it to select base application.")

    # List base files for that job_type
    base_bucket_s3_path = f"{get_settings().FL_APP_BASE_BUCKET}/src/{job_type.value}"
    logger.debug(f"Base bucket: {base_bucket_s3_path}")
    base_files = s3.list_objects(base_bucket_s3_path)
    if not base_files:
        raise FileNotFoundError("Base application files missing on the S3 bucket")

    # Clear destination if files already exist there (e.g. from a previous training run)
    dest_files = s3.list_objects(dest_bucket_s3_path)
    if dest_files:
        s3.delete_objects(dest_files)

    # Copy entire base tree into destination (1:1 paths under base_bucket_s3_path)
    for src_key in base_files:
        rel = src_key.replace(f"{base_bucket_s3_path}/", "", 1)
        dst_key = f"{dest_bucket_s3_path}/{rel}"
        logger.debug(f"Copying base {src_key} -> {dst_key}")
        s3.copy_object(src_key, dst_key)

    # Find app folders (top-level directories that start with "app", e.g. app_site1, app_site2, etc)
    # Retrieve the name of the app folders from the base_files
    app_folders: set[str] = set()
    for src_key in base_files:
        rel = src_key.replace(f"{base_bucket_s3_path}/", "", 1)
        logger.debug(f"Checking base file for app folder: {rel}")
        top = rel.split("/", 1)[0]  # e.g. "app_site1"
        if top.startswith("app"):
            app_folders.add(top)

    if not app_folders:
        raise FileNotFoundError(f"No app folders found under base application: {base_bucket_s3_path}")

    logger.debug(f"App folders found: {sorted(app_folders)}")

    # Validate required model files exist for the job type
    required_files = JobRequiredFiles.get_required_files(job_type)
    model_rel = {
        k.replace(f"{model_bucket_s3_path}/", "", 1) for k in model_files
    }  # relative paths of model files (i.e. without the bucket prefix)
    missing_files = [f for f in required_files if f not in model_rel]
    if len(missing_files) > 0:
        raise FileNotFoundError(f"Missing required files for job type {job_type.value}: {', '.join(missing_files)}. ")  # type: ignore[attr-defined]

    # Copy base application files to the destination bucket
    for file in base_files:
        # extract the rest of the file after the parent s3 path to copy the file tree structure
        key = file.replace(f"{base_bucket_s3_path}/", "")
        dest_file_path = f"{dest_bucket_s3_path}/{key}"

        logger.debug(f"Copying {file} to {dest_file_path}")
        s3.copy_object(file, dest_file_path)

    # Copy meta.json file from model files (if it exists) to the destination bucket
    if f"{model_bucket_s3_path}/meta.json" in model_files:
        src_meta_path = f"{model_bucket_s3_path}/meta.json"
        dest_meta_path = f"{dest_bucket_s3_path}/meta.json"
        logger.debug(f"Copying meta.json {src_meta_path} -> {dest_meta_path}")
        s3.copy_object(src_meta_path, dest_meta_path)

    # Copy model files into each app*/custom/, skipping meta.json
    for src_key in model_files:
        rel = src_key.replace(f"{model_bucket_s3_path}/", "", 1)

        # Skip meta.json as it is already copied
        if rel == "meta.json":
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
        base_files=base_files,
        model_files=model_files,
        app_folders=app_folders,
        base_bucket_s3_path=base_bucket_s3_path,
        model_bucket_s3_path=model_bucket_s3_path,
        dest_bucket_s3_path=dest_bucket_s3_path,
    )

    return job_type


def verify_bundle_paths(
    *,
    s3: "S3Client",
    base_files: list[str],
    model_files: list[str],
    app_folders: set[str],
    base_bucket_s3_path: str,
    model_bucket_s3_path: str,
    dest_bucket_s3_path: str,
) -> None:
    """
    Verifies that all expected destination keys exist after bundling.
    """

    # Relative paths of model files
    model_rel = {k.replace(f"{model_bucket_s3_path}/", "", 1) for k in model_files}

    # Construct the set of expected destination keys based on the base files, model files, and app folders
    expected: set[str] = set()

    # Base files (mirrored exactly)
    for src_key in base_files:
        rel = src_key.replace(f"{base_bucket_s3_path}/", "", 1)
        expected.add(f"{dest_bucket_s3_path}/{rel}")

    # meta.json copied once
    if "meta.json" in model_rel:
        expected.add(f"{dest_bucket_s3_path}/meta.json")

    # Model files copied into each app/custom (skip meta.json)
    for rel in model_rel:
        if rel == "meta.json":
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


def get_bundle_urls(model_id: UUID) -> List[str]:
    """
    Creates pre-signed URLs for the bundle files in S3 (containing the application files and model files) that the FL
    API will use for training.

    Args:
        model_id (UUID): The ID of the model to get the bundle URLs for.

    Returns:
        List[str]: A list of pre-signed URLs for the model bundle files.

    Raises:
        ClientError: If there is an error listing objects or generating pre-signed URLs.
    """
    s3_path = f"{get_settings().FL_APP_DESTINATION_BUCKET}/{model_id}"

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


def extract_current_job_data(net_endpoint: str, fl_backend_job_id: str) -> IJobMetaData:
    """
    Extract the current job data from the FL server status response.

    Args:
        net_endpoint (str): The endpoint of the Flare Loader service.
        fl_backend_job_id (str): The FL job ID to look for.

    Returns:
        IJobMetaData: The current job data if found.
    """
    url = f"{net_endpoint}/list_jobs"
    current_job_data = http_get(url)
    logger.debug(f"Current job data: {current_job_data}")

    # Validate the response format
    if not isinstance(current_job_data, list):
        error_msg = f"Unexpected response format from {url}: {current_job_data}"
        logger.error(error_msg)
        raise ValueError(error_msg)

    current_job_data = [IJobMetaData.model_validate(j) for j in current_job_data]

    # Get the running jobs only
    current_job_data = [j for j in current_job_data if j.status == "RUNNING"]
    logger.debug(f"Running jobs: {current_job_data}")

    # Filter the fl_backend_job_id
    current_job_data = [j for j in current_job_data if j.job_id == fl_backend_job_id]
    logger.debug(f"Current job data for job ID {fl_backend_job_id}: {current_job_data}")

    if not current_job_data:
        error_msg = f"Could not find job ID {fl_backend_job_id} on FL server {net_endpoint}."
        logger.error(error_msg)
        raise ValueError(error_msg)

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
        request: The FastAPI request object
        model_id: The ID of the model to abort
        session: SQLModel session object
    """
    logger.debug(f"Checking if model {model_id} is currently running...")

    try:
        from flip_api.fl_services.services import fl_scheduler_service

        # Always try to remove the job from queue
        fl_scheduler_service.remove_job_from_queue(model_id, session)

        fl_backend_job_id = get_fl_backend_job_id_by_model_id(model_id, session)
        net_details = fl_scheduler_service.get_net_by_model_id(model_id, session)
        net_endpoint = net_details.endpoint
        net_name = net_details.name

        logger.info(f"Net info for model {model_id}: endpoint={net_endpoint}, name={net_name}")

    except Exception as e:
        logger.info(f"Model {model_id} not currently running training; removed from queue. Reason: {e}")
        return

    request_id = str(request.scope.get("request_id", ""))
    server_status = fetch_server_status(request_id, net_endpoint)
    logger.debug(f"Server status: {server_status}")

    if not server_status:  # or server_status.status != FLStatus.SUCCESS.value:
        error_msg = f"FL Server not running for {model_id=}. Server status: {server_status}"
        logger.error(error_msg)
        raise ValueError(error_msg)

    # Extracting current job data from the server status
    current_job_data = extract_current_job_data(net_endpoint, fl_backend_job_id)

    # Current server job name (i.e. app_name) must match the model_id in order to abort
    current_app_name = current_job_data.job_name

    if current_app_name != str(model_id):
        error_msg = (
            f"Requested model to abort ({model_id=}) does not match the current model running on the server "
            f"({current_app_name=})."
        )
        logger.error(error_msg)
        raise ValueError(error_msg)

    # Extracting target and clients from the request path parameters
    path_params = request.path_params
    target = path_params.get("target")
    clients = path_params.get("clients")

    # Checking if the target provided is valid
    if target and target not in FLTargets:
        logger.error(f"Invalid target: {target}")
        raise ValueError(f"Invalid target: {target}")

    logger.debug(f"Attempting abort request for model ID: {model_id} on {net_name} (job ID: {fl_backend_job_id})")

    response = abort_job(request_id, net_endpoint, fl_backend_job_id)

    logger.info(f"Abort job response ({target=}, {clients=}): {response}")


def add_fl_job(model_id: UUID, clients: List[str], session: Session) -> None:
    """
    Insert a new FL job into the database.

    Args:
        model_id (UUID): The ID of the model for which the FL job is being created.
        clients (List[str]): A list of client names associated with the FL job.
        session (Session): The SQLModel session to use for the database operation.

    Raises:
        Exception: If there is an error during the database operation.
    """
    logger.debug(f"Adding FL job for model ID: {model_id}")

    job = FLJob(model_id=model_id, clients=clients)

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

    with Session(engine) as db:
        nets = fl_scheduler_service.get_nets(db)

    # For each FL Net in the database, call its check_status endpoint, which in turn calls the FL session.
    # NOTE In the old implementation, we had 3 'nets' in the database, each with its own FLAdminAPI. So each net had a
    # separate FLAdminAPI endpoint. Here, there should just be 1 net for now. If we add more nets in the future, they
    # might all have the same FLARE_API endpoint, if the FLARE_API controls all controllers/clients.
    for net in nets:
        try:
            fetch_server_status("", net.endpoint)
        except Exception as e:
            logger.error(f"Failed to send check request: {e}")
