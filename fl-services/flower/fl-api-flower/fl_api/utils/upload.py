import json
import shutil
from enum import Enum
from pathlib import Path
from urllib.parse import urlparse

import requests
from fastapi import HTTPException
from tomlkit import dumps, parse

from fl_api.schemas import UploadAppRequest
from fl_api.utils.logger import logger


class CheckpointExtension(str, Enum):
    """Allowed file extensions that are treated as model checkpoints and routed to the
    shared checkpoints directory instead of the upload directory."""

    PT = ".pt"
    PTH = ".pth"

    @classmethod
    def values(cls) -> set[str]:
        return {member.value for member in cls}


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


def _is_checkpoint_file(path: Path) -> bool:
    """Check if a file is a model checkpoint based on its extension.

    Args:
        path: Path object to check

    Returns:
        True if the file extension is in CheckpointExtension enum values
    """
    return path.suffix.lower() in CheckpointExtension.values()


def upsert_flwr_run_config(config_path: Path, model_id: str, project_id: str, cohort_query: str) -> None:
    """Insert or update the FLIP runtime parameters as top-level keys in config.toml.

    config.toml is the Flower ``--run-config`` override file: Flower reads the full app
    config from pyproject.toml and applies these keys on top of it. Any other content the
    researcher placed in config.toml is preserved.

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


def upload_application(
    model_id: str, body: UploadAppRequest, upload_dir: Path, checkpoints_dir: Path
) -> dict[str, str]:
    """
    Handles the logic of uploading an application to the server. This involves downloading the files uploaded by the
    user to a specific location on the server, and then returning a success message.

    Args:
        model_id (str): The unique identifier for the model/app being uploaded. This is used to determine where to
        store the uploaded files.
        body (UploadAppRequest): The body of the upload request, containing details such as project_id, cohort_query.
        upload_dir (Path): The base directory on the server where uploaded applications should be stored.
        checkpoints_dir (Path): The base directory where model checkpoint files (.pt, .pth) should be stored.
            This is a separate volume shared between the FL API and the SuperLink (and SuperNodes) so that
            evaluation apps can load checkpoints by model_id.

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

    # Then we create the job directory.
    job_dir.mkdir(parents=True, exist_ok=True)

    # Per-model checkpoints directory shared with the SuperLink/SuperNodes.
    # Checkpoint files (.pt, .pth) are routed here so they are not bundled into the
    # Flower app sent to SuperNodes, but remain accessible to evaluation apps.
    model_checkpoints_dir = checkpoints_dir / model_id
    if model_checkpoints_dir.exists():
        logger.warning(f"Model checkpoints directory {model_checkpoints_dir} already exists, removing it...")
        shutil.rmtree(model_checkpoints_dir, ignore_errors=True)
    model_checkpoints_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Downloading {len(bundle_urls)} files into job directory: {job_dir}")

    for url in bundle_urls:
        logger.info(f"Downloading file from {url}")

        # Reconstruct structure under app_dir using the URL path after model_id
        relative_path = _key_after_model_id(url, model_id)  # e.g. app/config.toml

        # Route checkpoint files to the shared checkpoints directory (flat layout: only filename)
        if _is_checkpoint_file(relative_path):
            dest_path = model_checkpoints_dir / relative_path.name
            logger.info(f"Routing checkpoint file to shared checkpoints dir: {dest_path}")
        else:
            dest_path = job_dir / relative_path  # job_dir/app/config.toml

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
    pyproject_toml = job_dir / "pyproject.toml"

    # Create an empty config.toml if the researcher did not upload one
    if not config_toml.exists():
        # If config.toml is not found, we create a default empty one to add FLIP configuration
        logger.warning(f"config.toml not found at expected location: {config_toml}. Will create an empty one.")
        config_toml.write_text("")

    # Now we add FLIP configuration as top-level run-config key/value pairs in config.toml
    upsert_flwr_run_config(config_toml, model_id, body.project_id, body.cohort_query)

    logger.info("config.toml updated with FLIP runtime parameters")

    response = {"message": f"Application uploaded successfully to: {job_dir}"}

    logger.info(response)

    return response
