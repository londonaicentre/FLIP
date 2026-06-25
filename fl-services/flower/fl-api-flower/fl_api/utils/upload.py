import shutil
from pathlib import Path
from urllib.parse import urlparse

import requests
from fastapi import HTTPException
from tomlkit import dumps, parse

from fl_api.schemas import UploadAppRequest
from fl_api.utils.logger import logger
from fl_api.utils.validation import safe_join, validate_bundle_url, validate_model_id


def _key_after_model_id(url: str, model_id: str) -> Path:
    """
    Extract only the path portion (query string is ignored automatically)

    For example, if the URL is https://example.com/model_id/config.json?version=1, and model_id is "model_id",
    this function will return Path("config.json")
    """
    path = urlparse(url).path.lstrip("/")

    parts = path.split("/")

    if model_id not in parts:
        raise ValueError(f"{model_id} not found in URL path: {path}")

    index = parts.index(model_id)

    # Everything after model_id
    return Path(*parts[index + 1 :])


def upsert_flwr_run_config(config_path: Path, model_id: str, project_id: str, cohort_query: str) -> None:
    """Insert or update the FLIP runtime parameters as top-level keys in config.toml.

    config.toml is the Flower ``--run-config`` override file: Flower reads the full app
    config from pyproject.toml and applies these keys on top of it. Any other content the
    researcher placed in config.toml is preserved.

    ``flip-job-dir`` is not set here — ``submit_run`` (in app.py) passes it as an
    inline ``--run-config`` override at submission time.

    Args:
        config_path: Path to the app's config.toml file.
        model_id: Model ID to inject as ``flip-model-id``.
        project_id: Project ID to inject as ``flip-project-id``.
        cohort_query: Cohort query to inject as ``flip-cohort-query``.
    """
    doc = parse(config_path.read_text()) if config_path.exists() else parse("")

    # run config values must be top-level key/value pairs in config.toml
    doc["flip-model-id"] = model_id
    doc["flip-project-id"] = project_id
    doc["flip-cohort-query"] = cohort_query

    config_path.write_text(dumps(doc))


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

    # model_id is the path component for the job dir; flip-api always sends a uuid4, so
    # anything else is rejected before it can traverse out of the upload dir.
    validate_model_id(model_id)

    # This section takes care of taking every uploaded file and copying it to the model_id path.

    bundle_urls = body.bundle_urls  # Retrieve the files that the user has uploaded to the platform.

    # We create the job app in the upload dir folder
    job_dir = safe_join(upload_dir, model_id)

    # If the job directory already exists, we remove it to avoid conflicts with previous uploads.
    if job_dir.exists():
        logger.warning(f"Job directory {job_dir} already exists, removing it...")
        shutil.rmtree(job_dir, ignore_errors=True)

    # Then we create the job directory.
    job_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Downloading {len(bundle_urls)} files into job directory: {job_dir}")

    for url in bundle_urls:
        logger.info(f"Downloading file from {url}")

        # The FL API fetches each URL server-side, so reject non-https / off-origin URLs.
        validate_bundle_url(url)

        # Reconstruct structure under job_dir using the URL path after model_id
        relative_path = _key_after_model_id(url, model_id)  # e.g. app/config.toml
        dest_path = safe_join(job_dir, *relative_path.parts)  # contained under job_dir
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with requests.get(url, stream=True, timeout=60) as resp:
                resp.raise_for_status()
                with open(dest_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)
        except Exception as e:
            logger.error(f"Failed to download file from {url} with error: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to download file from {url}: {e}")

        logger.info(f"Downloaded file {dest_path}")

    # Part 2: optional config.toml file
    # among the uploaded files, there may be an override config.toml file
    # populate the config.toml file with the FLIP configuration parameters (model_id, project_id, cohort_query)
    config_toml = job_dir / "app" / "config.toml"

    # Create an empty config.toml if the researcher did not upload one (needed to inject FLIP runtime parameters)
    if not config_toml.exists():
        logger.warning(f"config.toml not found at expected location: {config_toml}. Will create an empty one.")
        config_toml.parent.mkdir(parents=True, exist_ok=True)
        config_toml.write_text("")

    # Now we add FLIP configuration as top-level run-config key/value pairs in config.toml
    upsert_flwr_run_config(config_toml, model_id, body.project_id, body.cohort_query)

    logger.info("config.toml updated with FLIP runtime parameters")

    response = {"message": f"Application uploaded successfully to: {job_dir}"}

    logger.info(response)

    return response
