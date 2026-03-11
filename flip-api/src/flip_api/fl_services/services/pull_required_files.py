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
from pathlib import Path

from flip_api.config import get_settings
from flip_api.utils.constants import JOB_TYPES_REQUIRED_FILES_FILE
from flip_api.utils.logger import logger
from flip_api.utils.s3_client import S3Client

# Path to the JSON file containing job types and required files (relative to this file)
JOB_TYPES_REQUIRED_FILES_PATH = Path(__file__).parent.parent.parent / "assets" / JOB_TYPES_REQUIRED_FILES_FILE


# TODO Review: This is disruptive for development if the file on the s3 bucket is not in sync with the current codebase.
def pull_required_files_json_to_assets():
    """
    Pulls required_files.json from S3 and saves it to the local assets folder.
    """
    s3 = S3Client()
    bucket_path = f"{get_settings().FL_APP_BASE_BUCKET}/src/required_files.json"

    try:
        s3_obj = s3.get_object(bucket_path)
        content = s3_obj["Body"].read()
        # Validate JSON
        json.loads(content)
        with open(JOB_TYPES_REQUIRED_FILES_PATH, "wb") as f:
            f.write(content)
        logger.info(f"Downloaded required_files.json to {JOB_TYPES_REQUIRED_FILES_PATH}")
    except Exception as e:
        logger.error(f"Failed to download {JOB_TYPES_REQUIRED_FILES_FILE}: {e}")
        # Fallback to default JSON
        if get_settings().FL_BACKEND == "nvflare":
            fallback = {"standard": ["trainer.py", "validator.py", "models.py", "config.json"]}
        elif get_settings().FL_BACKEND == "flower":
            fallback = {"standard": ["client_app.py", "models.py"]}
        else:
            fallback = {}
        with open(JOB_TYPES_REQUIRED_FILES_PATH, "w") as f:
            json.dump(fallback, f, indent=4)
        logger.info(f"Wrote fallback required_files.json to {JOB_TYPES_REQUIRED_FILES_PATH}")
