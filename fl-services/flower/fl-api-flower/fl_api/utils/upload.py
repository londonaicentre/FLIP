import shutil
from pathlib import Path
from urllib.parse import urlparse

import requests

from fl_api.schemas import UploadAppRequest
from fl_api.utils.io_utils import read_config, write_config
from fl_api.utils.logger import logger


def _key_after_model_id(url: str, model_id: str) -> Path:
    """
    Extract only the path portion (query string is ignored automatically)

    For example, if the URL is https://example.com/model_id/app/config.yaml?version=1, and model_id is "model_id",
    this function will return Path("app/config.yaml")
    """
    path = urlparse(url).path.lstrip("/")

    parts = path.split("/")

    if model_id not in parts:
        raise ValueError(f"{model_id} not found in URL path: {path}")

    index = parts.index(model_id)

    # Everything after model_id
    return Path(*parts[index + 1 :])


def upload_application(model_id: str, body: UploadAppRequest, upload_dir: Path) -> dict[str, str]:
    """
    Handles the logic of uploading an application to the server. This involves downloading the files uploaded by the
    user to a specific location on the server, and then returning a success message.

    Args:
        model_id (str): The unique identifier for the model/app being uploaded. This is used to determine where to
        store the uploaded files.
        body (UploadAppRequest): The body of the upload request, containing details such as project_id, cohort_query.
        upload_dir (Path): The base directory on the server where uploaded applications should be stored.

    Returns:
        dict[str, str]: A dictionary containing a success message and the location where the application was uploaded.
    """
    logger.info(f"Received request to upload app: {model_id}")

    # This section takes care of taking every uploaded file and copying it to the model_id path.

    bundle_urls = body.bundle_urls  # Retrieve the files that the user has uploaded to the platform.

    # We create the job app in the upload dir folder
    job_dir = Path.cwd() / upload_dir / model_id

    # If the job directory already exists, we remove it to avoid conflicts with previous uploads.
    if job_dir.exists():
        logger.warning(f"Job directory {job_dir} already exists, removing it...")
        shutil.rmtree(job_dir, ignore_errors=True)

    # Then we create the app directory for the job.
    app_dir = job_dir / "app"
    app_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Downloading {len(bundle_urls)} files into job directory: {job_dir}")

    for url in bundle_urls:
        logger.info(f"Downloading file from {url}")

        # Reconstruct structure under app_dir using the URL path after model_id
        relative_path = _key_after_model_id(url, model_id)  # e.g. app/config.yaml
        dest_path = app_dir / relative_path  # job_dir/app/app/config.yaml (see note below)

        dest_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with requests.get(url, stream=True, timeout=60) as resp:
                resp.raise_for_status()
                with open(dest_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)
        except Exception as e:
            logger.error(f"Failed to download from URL {url} with error: {e}")
            raise

        logger.info(f"Downloaded file {dest_path}")

    # Part 2: add project_id and query to config.json
    config_path = app_dir / "config.json"
    if config_path.exists():
        config = read_config(config_path)
    else:
        config = {}

    config["project_id"] = body.project_id
    config["cohort_query"] = body.cohort_query

    write_config(config, config_path)

    response = {"message": f"Application uploaded successfully to: {job_dir}"}

    logger.info(response)

    return response
