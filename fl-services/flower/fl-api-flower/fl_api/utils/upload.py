import json
import shutil
from enum import Enum
from pathlib import Path
from urllib.parse import urlparse

import requests
from fastapi import HTTPException
from tomlkit import dumps, parse, table

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


def upsert_flwr_run_config(
    config_path: Path,
    model_id: str,
    project_id: str,
    cohort_query: str,
    checkpoint_files: list[str] | None = None,
    app_config_path: Path | None = None,
    pyproject_path: Path | None = None,
) -> None:
    """Merge config.toml into pyproject.toml and rewrite config.toml with only FLIP parameters.

    Process:
    1. Read user's config.toml (has [models], [metrics] in simplified format)
    2. Merge into pyproject.toml under [tool.flwr.app.config]
    3. Overwrite config.toml with ONLY FLIP runtime parameters
    4. Flower reads full config from pyproject.toml, overrides from config.toml via --run-config

    Args:
        config_path: Path to config.toml file
        model_id: Model ID to inject
        project_id: Project ID to inject
        cohort_query: Cohort query to inject
        checkpoint_files: Not used (kept for compatibility)
        app_config_path: Not used (kept for compatibility)
        pyproject_path: Path to pyproject.toml to update
    """
    if not pyproject_path or not pyproject_path.exists():
        logger.error(f"pyproject.toml not found at {pyproject_path}")
        return

    # Step 1: Read config.toml (simplified format: [models], [metrics], etc.)
    user_sections = {}
    if config_path.exists():
        config_doc = parse(config_path.read_text())
        for key, value in config_doc.items():
            if isinstance(value, dict):
                user_sections[key] = value
        logger.info(f"Loaded config.toml with sections: {list(user_sections.keys())}")

    # Step 2: Merge into pyproject.toml [tool.flwr.app.config]
    pyproject_doc = parse(pyproject_path.read_text())

    if (
        "tool" in pyproject_doc  # type: ignore[operator]
        and "flwr" in pyproject_doc["tool"]  # type: ignore[operator,index]
        and "app" in pyproject_doc["tool"]["flwr"]  # type: ignore[operator,index]
        and "config" in pyproject_doc["tool"]["flwr"]["app"]  # type: ignore[operator,index]
    ):
        config_section = pyproject_doc["tool"]["flwr"]["app"]["config"]  # type: ignore[index]

        # Merge user sections (models, metrics, etc.)
        for section_name, section_content in user_sections.items():
            new_section = table()
            for key, value in section_content.items():
                if isinstance(value, dict):
                    # Nested table (e.g., models.model_name)
                    nested_table = table()
                    for k, v in value.items():
                        nested_table[k] = v
                    new_section[key] = nested_table
                else:
                    new_section[key] = value
            config_section[section_name] = new_section  # type: ignore[index]
            logger.info(f"Merged [{section_name}] into pyproject.toml [tool.flwr.app.config.{section_name}]")

        # Write updated pyproject.toml
        pyproject_path.write_text(dumps(pyproject_doc))
        logger.info(f"Updated pyproject.toml with {len(user_sections)} section(s) from config.toml")
    else:
        logger.error("pyproject.toml missing [tool.flwr.app.config] section")
        return

    # Step 3: Overwrite config.toml with ONLY FLIP parameters
    clean_config = table()
    clean_config["flip-model-id"] = model_id
    clean_config["flip-project-id"] = project_id
    clean_config["flip-cohort-query"] = cohort_query

    config_path.write_text(dumps(clean_config))
    logger.info("Rewrote config.toml with only FLIP runtime parameters")


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

    # Track uploaded checkpoint files to configure in config.toml
    checkpoint_files: list[str] = []

    for url in bundle_urls:
        logger.info(f"Downloading file from {url}")

        # Reconstruct structure under app_dir using the URL path after model_id
        relative_path = _key_after_model_id(url, model_id)  # e.g. app/config.toml

        # Route checkpoint files to the shared checkpoints directory (flat layout: only filename)
        if _is_checkpoint_file(relative_path):
            dest_path = model_checkpoints_dir / relative_path.name
            checkpoint_files.append(relative_path.name)  # Track for config.toml
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
            logger.error(f"Failed to download from URL {url} with error: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to download file from {url}: {e}")

        logger.info(f"Downloaded file {dest_path}")

    # Part 2: Move app/config.toml to root, merge into pyproject.toml, then clean config.toml
    config_toml = job_dir / "config.toml"
    app_config_toml = job_dir / "app" / "config.toml"
    pyproject_toml = job_dir / "pyproject.toml"

    # If app/config.toml exists, move it to root level
    if app_config_toml.exists() and not config_toml.exists():
        logger.info(f"Moving {app_config_toml} to {config_toml}")
        shutil.move(str(app_config_toml), str(config_toml))

    # Create empty config.toml if none exists
    if not config_toml.exists():
        logger.warning("No config.toml found. Creating empty root config.toml")
        config_toml.write_text("")

    # Part 3: Read job_type from config.json and validate BEFORE merging config
    # The FLIP UI displays the job_type to inform users whether this is a training or evaluation app
    job_type = "standard"  # Default to standard if config.json is missing or doesn't specify
    config_json = job_dir / "config.json"
    if config_json.exists():
        try:
            config_data = json.loads(config_json.read_text())
            job_type = config_data.get("job_type", "standard")
            logger.info(f"Detected job_type from config.json: {job_type}")
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse config.json: {e}. Using default job_type='standard'")
        except Exception as e:
            logger.warning(f"Error reading config.json: {e}. Using default job_type='standard'")

    # Part 4: Validate that evaluation apps have models configuration in config.toml BEFORE cleaning it
    if job_type == "evaluation":
        if not config_toml.exists():
            logger.error("Evaluation apps require config.toml with model configuration")
            raise HTTPException(status_code=500, detail="Evaluation apps require config.toml with [models] section")

        # Check if config.toml has models section (simplified format: [models] not [tool.flwr.app.config.models])
        # Flower automatically treats this as [tool.flwr.app.config.models] when using --run-config
        config_doc = parse(config_toml.read_text())
        has_models = "models" in config_doc and len(config_doc["models"]) > 0  # type: ignore[arg-type]

        if not has_models:
            logger.error("Evaluation app config.toml missing [models] section")
            raise HTTPException(
                status_code=500,
                detail=(
                    "Evaluation apps require config.toml with [models] section "
                    "defining model checkpoints (e.g., [models.model_name] with checkpoint and path fields)"
                ),
            )

        logger.info(f"Validated evaluation app has models configuration in {config_toml}")

    # Part 5: Merge config.toml with FLIP parameters (keeps all user content: models, metrics, etc.)
    upsert_flwr_run_config(
        config_toml,
        model_id,
        body.project_id,
        body.cohort_query,
        checkpoint_files=checkpoint_files if checkpoint_files else None,
        app_config_path=app_config_toml if app_config_toml.exists() else None,
        pyproject_path=pyproject_toml if pyproject_toml.exists() else None,
    )

    logger.info("config.toml updated with FLIP parameters while preserving all user content")

    response = {
        "message": f"Application uploaded successfully to: {job_dir}",
        "job_type": job_type,
    }

    logger.info(response)

    return response
