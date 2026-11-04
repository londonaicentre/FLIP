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

import contextlib
import time

from nvflare.apis.fl_exception import FLCommunicationError

from fl_api.config import get_settings
from fl_api.utils.flip_session import FLIP_Session
from fl_api.utils.job_resources import default_job_resources
from fl_api.utils.logger import logger


def create_fl_session() -> FLIP_Session:
    """
    Initialize NVIDIA FLARE admin workspace and return a session object for interacting with it.

    Returns:
        FLIP_Session: An initialized FLIP_Session object.
    """
    username = get_settings().USERNAME
    admin_dir = get_settings().FL_ADMIN_DIRECTORY
    secure_mode = get_settings().SECURE_MODE
    debug = get_settings().LOG_LEVEL == "DEBUG"

    logger.info(f"Admin directory set to: {admin_dir}")
    # Refuse to start on a default the request contract rejects: every job that falls back to it would fail.
    default_resources = default_job_resources()
    logger.info(f"GPU request for a job that declares none: {default_resources.model_dump()}")
    logger.info(f"Using LOG_LEVEL: {get_settings().LOG_LEVEL}")

    # Connect here so a misconfigured admin kit fails at boot. The FL server may still be starting (it can take
    # minutes on a loaded host), so keep trying until TIMEOUT_FL_SERVER_STARTUP: a single attempt left the
    # API up but dead, with the failed session's connector retrying forever behind it.
    deadline = time.monotonic() + get_settings().TIMEOUT_FL_SERVER_STARTUP
    while True:
        session = FLIP_Session(username=username, startup_path=admin_dir, secure_mode=secure_mode, debug=debug)
        try:
            session.try_connect(get_settings().TIMEOUT_SESSION_CONNECT)
            break
        except FLCommunicationError as e:
            with contextlib.suppress(Exception):
                session.close()
            if time.monotonic() >= deadline:
                raise
            logger.warning(f"FL server not reachable yet ({e}); retrying")

    logger.info(f"Upload directory set to: {session.upload_dir}")
    logger.info(f"Download directory set to: {session.download_dir}")

    return session
