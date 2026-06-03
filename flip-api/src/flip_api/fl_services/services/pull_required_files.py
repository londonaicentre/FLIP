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

from flip_api.config import get_settings
from flip_api.domain.interfaces.fl import required_job_types_file
from flip_api.domain.schemas.types import FLBackend
from flip_api.utils.constants import job_types_required_files_s3_key
from flip_api.utils.logger import logger
from flip_api.utils.s3_client import S3Client


def pull_required_files_json_to_assets(fl_backend: FLBackend) -> None:
    """Pulls the per-backend ``required_files.json`` from S3 into the local assets folder.

    S3 is the single source of truth — there is no checked-in or in-code fallback. If the
    download fails the error is logged and re-raised; callers decide whether to proceed
    (model creation treats this as best-effort). This mirrors the base-application contract:
    without S3 there is nothing to bundle.

    Args:
        fl_backend (FLBackend): The FL backend whose manifest to pull (``nvflare`` or ``flower``).

    Raises:
        Exception: If the object cannot be fetched from S3 or is not valid JSON.
    """
    s3 = S3Client()
    bucket_path = f"{get_settings().FL_APP_BASE_BUCKET}/{job_types_required_files_s3_key(fl_backend)}"
    dest = required_job_types_file(fl_backend)

    try:
        s3_obj = s3.get_object(bucket_path)
        content = s3_obj["Body"].read()
        # Validate JSON before writing so we never persist a corrupt manifest
        json.loads(content)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            f.write(content)
        logger.info(f"Downloaded required_files.json for [{fl_backend}] from {bucket_path} to {dest}")
    except Exception as e:
        logger.error(f"Failed to download required_files.json for [{fl_backend}] from {bucket_path}: {e}")
        raise
