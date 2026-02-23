import shutil
from pathlib import Path
from urllib.parse import urlparse

import requests
from tomlkit import dumps, parse

from fl_api.schemas import UploadAppRequest
from fl_api.utils.logger import logger


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


def upsert_flwr_run_config(pyproject_path: Path, model_id: str, project_id: str, cohort_query: str) -> None:
    """This function updates the pyproject.toml file with the provided model_id, project_id and cohort_query."""
    doc = parse(pyproject_path.read_text())

    doc["tool"]["flwr"]["app"]["config"]["flip-model-id"] = model_id  # type: ignore[index]
    doc["tool"]["flwr"]["app"]["config"]["flip-project-id"] = project_id  # type: ignore[index]
    doc["tool"]["flwr"]["app"]["config"]["flip-cohort-query"] = cohort_query  # type: ignore[index]

    pyproject_path.write_text(dumps(doc))


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
    job_dir = upload_dir / model_id

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
        relative_path = _key_after_model_id(url, model_id)  # e.g. config.json
        dest_path = app_dir / relative_path  # job_dir/app/config.json

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

    # Part 2: pyproject.toml file
    # among the uploaded files, there should be a pyproject.toml file which needs to go 1 folder above app_dir
    # i.e. to job_dir, so we move it there.
    pyproject_src = app_dir / "pyproject.toml"
    pyproject_dest = job_dir / "pyproject.toml"
    if pyproject_src.exists():
        shutil.move(str(pyproject_src), str(pyproject_dest))
        logger.info(f"Moved pyproject.toml from {pyproject_src} to {pyproject_dest}")
    else:
        logger.error(f"pyproject.toml not found at expected location: {pyproject_src}")

    # Now we add project_id and query to flwr run config section in pyproject.toml
    upsert_flwr_run_config(pyproject_dest, model_id, body.project_id, body.cohort_query)

    response = {"message": f"Application uploaded successfully to: {job_dir}"}

    logger.info(response)

    return response
