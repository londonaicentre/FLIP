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

from fastapi import HTTPException, status
from fl_api.config import get_settings
from fl_api.utils.flip_session import new_secure_Flip_session
from fl_api.utils.logger import logger
from nvflare.apis.workspace import Workspace
from nvflare.fuel.common.excepts import ConfigError
from nvflare.fuel.hci.client.config import secure_load_admin_config
from nvflare.security.logging import secure_format_exception


def create_fl_session():
    """Initialize NVFlare admin workspace and return a secure session."""
    admin_dir = get_settings().FL_ADMIN_DIRECTORY

    logger.info(f"Admin directory set to: {admin_dir}")
    logger.info(
        f"Default GPU resource_spec for each job: {get_settings().JOB_RESOURCE_SPEC_NUM_GPUS} GPUs, "
        f"{get_settings().JOB_RESOURCE_SPEC_MEM_PER_GPU_IN_GIB} GiB per GPU"
    )
    logger.info(f"Using LOG_LEVEL: {get_settings().LOG_LEVEL}")

    try:
        os.chdir(admin_dir)
        workspace = Workspace(root_dir=admin_dir)
        conf = secure_load_admin_config(workspace)
        logger.info("secure_load_admin_config ran successfully:")
        logger.info(conf.config_data)
    except ConfigError as e:
        error_message = f"ConfigError: {secure_format_exception(e)}"
        logger.error(error_message)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_message,
        ) from e
    except Exception as e:
        error_message = f"Unexpected error during configuration: {secure_format_exception(e)}"
        logger.error(error_message)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_message,
        ) from e

    try:
        admin_config = conf.config_data["admin"]
    except KeyError:
        error_message = "Missing 'admin' section in fed_admin configuration."
        logger.error(error_message)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_message,
        )

    upload_dir = admin_config.get("upload_dir")
    download_dir = admin_config.get("download_dir")
    if not os.path.isdir(download_dir):
        os.makedirs(download_dir)

    logger.info(f"Upload directory set to: {upload_dir}")
    logger.info(f"Download directory set to: {download_dir}")

    assert os.path.isdir(admin_dir), f"admin directory does not exist at {admin_dir}"

    # We set up the session in the admin directory.
    session = new_secure_Flip_session(
        username="admin",
        startup_kit_location=admin_dir,
    )
    return session
