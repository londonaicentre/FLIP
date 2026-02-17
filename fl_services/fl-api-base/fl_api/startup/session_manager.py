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


from fl_api.config import get_settings
from fl_api.utils.flip_session import FLIP_Session
from fl_api.utils.logger import logger


def create_fl_session(username: str = "admin", secure_mode: bool = False, debug: bool = False) -> FLIP_Session:
    """
    Initialize NVFlare admin workspace and return a secure session.

    Args:
        username: The username for the FLIP session (default: "admin").
        secure_mode: Whether to enable secure mode for the session (default: False).
        debug: Whether to enable debug mode for the session (default: False).

    Returns:
        FLIP_Session: An initialized FLIP_Session object.
    """
    admin_dir = get_settings().FL_ADMIN_DIRECTORY

    logger.info(f"Admin directory set to: {admin_dir}")
    logger.info(
        f"Default GPU resource_spec for each job: {get_settings().JOB_RESOURCE_SPEC_NUM_GPUS} GPUs, "
        f"{get_settings().JOB_RESOURCE_SPEC_MEM_PER_GPU_IN_GIB} GiB per GPU"
    )
    logger.info(f"Using LOG_LEVEL: {get_settings().LOG_LEVEL}")

    # Set up the session
    session = FLIP_Session(
        username=username,
        startup_path=admin_dir,
        secure_mode=secure_mode,
        debug=debug,
    )

    # Try connecting the session here, so that we can catch any connection issues at startup
    session.try_connect(get_settings().TIMEOUT_SESSION_CONNECT)

    logger.info(f"Upload directory set to: {session.upload_dir}")
    logger.info(f"Download directory set to: {session.download_dir}")

    return session
