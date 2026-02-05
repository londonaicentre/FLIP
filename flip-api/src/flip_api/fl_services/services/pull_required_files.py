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

from flip_api.config import get_settings
from flip_api.utils.logger import logger
from flip_api.utils.s3_client import S3Client


# TODO Review: This is disruptive for development if the file on the s3 bucket is not in sync with the current codebase.
def pull_required_files_json_to_assets():
    """
    Pulls required_files.json from S3 (FL_APP_BASE_BUCKET/src/required_files.json)
    and saves it to the local assets folder.
    """
    s3 = S3Client()
    bucket_path = f"{get_settings().FL_APP_BASE_BUCKET}/src/required_files.json"
    assets_dir = os.path.join(os.path.dirname(__file__), "..", "..", "assets")
    os.makedirs(assets_dir, exist_ok=True)
    local_path = os.path.join(assets_dir, "required_job_types.json")
    try:
        s3_obj = s3.get_object(bucket_path)
        content = s3_obj["Body"].read()
        # Validate JSON
        json.loads(content)
        with open(local_path, "wb") as f:
            f.write(content)
        logger.info(f"Downloaded required_job_types.json to {local_path}")
    except Exception as e:
        logger.error(f"Failed to download required_files.json: {e}")
        # Fallback to default JSON
        fallback = {"standard": ["trainer.py", "validator.py", "models.py", "config.json"]}
        with open(local_path, "w") as f:
            json.dump(fallback, f, indent=4)
        logger.info(f"Wrote fallback required_job_types.json to {local_path}")
