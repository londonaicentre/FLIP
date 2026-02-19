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

import os
import shutil
from urllib.parse import urlparse

import requests
from fl_api.utils.constants import META
from fl_api.utils.logger import logger
from fl_api.utils.prepare_config import (
    configure_client,
    configure_config,
    configure_environment,
    configure_meta,
    configure_server,
)
from fl_api.utils.schemas import UploadAppRequest


def upload_application(model_id: str, body: UploadAppRequest, upload_dir: str) -> dict:
    """Uploads an application to the upload dir folder of the server.

    Downloads the files from the provided bundle_urls in the UploadAppRequest body,
    creates the necessary folder structure, and updates the configuration files accordingly.

    Args:
        model_id (str): id of the model.
        body (UploadAppRequest): UploadAppRequest object with info such as project_id, cohort_query, local_rounds,
        global_rounds, trusts, ignore_result_error, aggregator, and aggregation_weights.
        upload_dir (str): directory where the application will be uploaded (session's upload dir.)
    Raises:
        HTTPException: if the application fails to upload, an error is raised.
        FileNotFoundError: if the application path is not found, an error is raised.
        IsADirectoryError: if the URL object is a directory, an error is raised.
    Returns:
        dict: response
    """
    logger.info(f"Received request to upload app: {model_id}")

    # This section takes care of taking every uploaded file and copying it to the model_id path.

    bundle_urls = body.bundle_urls  # Retrieve the files that the user has uploaded to the platform.
    current_dir = os.path.abspath(os.curdir)

    logger.info(f"Current directory: {current_dir}")

    job_dir = os.path.join(current_dir, upload_dir, model_id)  # We create the job app in the upload dir folder

    if os.path.exists(job_dir):
        logger.warning(f"Job directory {job_dir} already exists, removing it...")
        shutil.rmtree(job_dir, ignore_errors=True)

    os.makedirs(os.path.join(job_dir, "app"), exist_ok=True)

    logger.info(f"Downloading {len(bundle_urls)} files into job directory: {job_dir}")

    # Initialise list to keep track of app folder names
    app_folder_names = []

    for url in bundle_urls:
        logger.info(f"Downloading file from {url}")

        try:
            path = urlparse(url).path
            downloaded_file = requests.get(url)
            content = downloaded_file.content
        except Exception as e:
            logger.error(f"Failed to download from URL {url} with error: {e}")
            raise e

        #
        s3_file_dir, file_name = os.path.split(path)

        if not file_name:
            # Empty directories are not uploaded: users can only upload files.
            logger.info(f"{path} looks like a directory, skipping...")
            continue

        elif file_name == META:
            relative_file_path = ""

        else:
            # We look for app. First, we remove whatever goes before the model ID to find it
            s3_file_dir_model = s3_file_dir.split(model_id)[-1]
            logger.debug(f"s3_file_dir_model: {s3_file_dir_model}")

            if "/app" in s3_file_dir_model:
                app_folder_name = f"app{s3_file_dir_model.split('/app')[-1].split('/')[0]}"
            else:
                app_folder_name = "app"

            # Append to app_folder_names if not already present
            if app_folder_name not in app_folder_names:
                app_folder_names.append(app_folder_name)

            if "/custom" in s3_file_dir:
                custom_split = s3_file_dir.split("/custom")
                if len(custom_split) > 2:
                    relative_file_path = f"{app_folder_name}/custom{'/'.join(custom_split[1:])}"
                else:
                    relative_file_path = f"{app_folder_name}/custom{custom_split[-1]}"

            elif "/config" in s3_file_dir:
                config_split = s3_file_dir.split("/config")
                if len(config_split) > 2:
                    relative_file_path = f"{app_folder_name}/config{'/'.join(config_split[1:])}"
                else:
                    relative_file_path = f"{app_folder_name}/config{config_split[-1]}"

            else:
                relative_file_path = "app/custom"

        # Create the directory if it does not exist
        abs_file_dir = os.path.join(job_dir, relative_file_path)
        if not os.path.exists(abs_file_dir):
            logger.info(f"Creating new directory: '{abs_file_dir}'")
            os.makedirs(abs_file_dir)

        # Finally we write the file content into the resolved location.
        file_path = os.path.join(abs_file_dir, file_name)
        with open(file_path, "wb") as file:
            file.write(content)
        logger.info(f"Downloaded file {file_path}")

    logger.info("Successfully downloaded application, updating application config...")

    # If you have different app folders, you need to provide the meta.json file.
    logger.debug(f"App folder names identified: {app_folder_names}")
    if len(app_folder_names) > 1:
        meta_json_path = os.path.join(job_dir, META)
        if not os.path.exists(meta_json_path):
            error_message = (
                f"Application must contain a {META} file in the root of the application folder if you have"
                "multiple app sub-folders indicating different behaviors per site."
            )
            logger.error(error_message)
            raise FileNotFoundError(error_message)

    # A federated learning job consists of, at least, a CONFIG and CUSTOM folder. Within CONFIG, two files,
    # config_fed_server.json and config_fed_client.json. Within custom, all uploaded files. In addition to
    # CONFIG and CUSTOM, a meta.json file has all the info concerning number of GPUs etc.
    # See more information in https://nvflare.readthedocs.io/en/2.6/real_world_fl/job.html#job

    # We run this configuration script for all of the different app folders.

    for app_folder_name in app_folder_names:
        logger.info(f"Configuring application folder: {app_folder_name}")
        app_folder_path = os.path.join(job_dir, app_folder_name)

        try:
            # Update config.json with the new global and local rounds
            configure_config(
                app_folder_path,
                global_rounds_override=body.global_rounds,
                local_rounds_override=body.local_rounds,
            )

            # Check if config_fed_client.json exists and verify its config.
            configure_client(
                app_folder_path,
                model_id,
                body.project_id,
                body.cohort_query,
            )

            # Check if config_fed_server.json exists and verify its config.
            configure_server(
                app_folder_path,
                model_id,
                body.global_rounds,  # I'd remove this one
                body.trusts,
                body.ignore_result_error,
                body.aggregator,
                body.aggregation_weights,
            )

            # Configure the environment.json file if it exists.
            configure_environment(app_folder_path)

            if app_folder_name == "app":
                # Write meta.json file
                configure_meta(job_dir, model_id, body.trusts)

        except Exception as e:
            logger.error(f"Error occurred while configuring application folder {app_folder_name}: {e}")
            raise e

    response = {"message": f"Application uploaded successfully to: {job_dir}"}

    logger.info(response)

    return response
