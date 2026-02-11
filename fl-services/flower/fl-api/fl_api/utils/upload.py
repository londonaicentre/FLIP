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

import shutil
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

from fl_api.utils.logger import logger
from fl_api.utils.schemas import UploadAppRequest


def _relative_dir_for_download(model_id: str, s3_file_dir: str) -> Path:
    """
    Return the directory (relative to job_dir) that mirrors the S3 structure.

    Example:
      s3_file_dir = "/flipdev-artifacts/models/<model_id>/app/site-1"
      -> Path("app/site-1")
    """
    # URL paths can contain %2F etc.
    s3_file_dir = unquote(s3_file_dir)

    marker = f"/{model_id}/"
    if marker not in s3_file_dir:
        # Fallback: if we can't find model_id in the path, dump into root
        return Path("")

    # Keep everything after "/<model_id>/"
    rel = s3_file_dir.split(marker, 1)[1].lstrip("/")
    return Path(rel)


def upload_application(model_id: str, body: UploadAppRequest, upload_dir: str) -> dict:
    """
    Uploads an application to the upload dir folder of the server.

    Downloads the files from the provided bundle_urls (AWS S3 pre-signed URLs) in the UploadAppRequest body, creates the
    necessary folder structure, and updates the configuration files accordingly.

    Args:
        model_id (str): id of the model.
        body (UploadAppRequest): UploadAppRequest object with config info such as project_id, cohort_query, etc
        upload_dir (str): directory where the application will be uploaded (session's upload dir.)

    Raises:
        FileNotFoundError: if the application path is not found, an error is raised.

    Returns:
        dict: response
    """
    logger.info(f"Received request to upload app: {model_id}")

    bundle_urls = body.bundle_urls
    job_dir = Path.cwd() / upload_dir / model_id

    if job_dir.exists():
        logger.warning(f"Job directory {job_dir} already exists, removing it...")
        shutil.rmtree(job_dir, ignore_errors=True)

    # Create job root
    job_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Downloading {len(bundle_urls)} files into job directory: {job_dir}")

    for url in bundle_urls:
        logger.info(f"Downloading file from {url}")

        path = unquote(urlparse(url).path)
        s3_file_dir = str(Path(path).parent)
        file_name = Path(path).name

        try:
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
            content = resp.content
        except Exception as e:
            logger.error(f"Failed to download from URL {url} with error: {e}")
            raise

        relative_dir = _relative_dir_for_download(model_id, s3_file_dir)

        dest_dir = job_dir / relative_dir
        dest_dir.mkdir(parents=True, exist_ok=True)

        dest_path = dest_dir / file_name
        dest_path.write_bytes(content)
        logger.info(f"Downloaded file {dest_path}")

    logger.info("Successfully downloaded application, updating application config...")

    response = {"message": f"Application uploaded successfully to: {job_dir}"}
    logger.info(response)
    return response
