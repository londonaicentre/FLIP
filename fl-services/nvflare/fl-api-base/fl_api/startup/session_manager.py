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


from nvflare.apis.fl_exception import FLCommunicationError
from nvflare.fuel.flare_api.api_spec import InternalError, NoConnection

from fl_api.config import get_settings
from fl_api.utils.flip_session import FLIP_Session
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

    logger.info(f"Upload directory set to: {session.upload_dir}")
    logger.info(f"Download directory set to: {session.download_dir}")

    # Try connecting the session here, so that we can catch any connection issues at startup.
    #
    # With PER_JOB_FL_SERVER off, an unreachable fl-server at boot is fatal (unchanged) — the
    # normal alerting signal for an unplanned outage. With it on, the server is expected to be
    # down most of the time (scaled to zero between jobs, FLIP#735), so transport failures are
    # tolerated and the session connects lazily on first use (FLIP_Session._do_command).
    #
    # Discrimination is deliberate: transport-down and not-ready are tolerable; auth/identity
    # failures are not. A wrong admin kit (AuthenticationError) or a server-identity mismatch
    # (FLCommunicationError, or NoConnection with "cannot authenticate") still raise either way.
    try:
        session.try_connect(get_settings().TIMEOUT_SESSION_CONNECT)
    except NoConnection as e:
        if "cannot authenticate" in str(e) or not get_settings().PER_JOB_FL_SERVER:
            raise
        logger.warning(
            "fl-server unreachable at boot (cannot connect); PER_JOB_FL_SERVER is on — treating "
            "this as the normal idle-between-jobs state. Will connect lazily on first use. "
            "Reason: %s",
            e,
        )
    except InternalError as e:
        if not get_settings().PER_JOB_FL_SERVER:
            raise
        logger.warning(
            "fl-server login failed at boot (server up but not ready); PER_JOB_FL_SERVER is on — "
            "treating this as a cold start. Will connect lazily on first use. Reason: %s",
            e,
        )
    except FLCommunicationError:
        # Rejected registration / server-identity mismatch — a misconfiguration, never the
        # idle-between-jobs state. Fatal regardless of the flag.
        raise

    return session
