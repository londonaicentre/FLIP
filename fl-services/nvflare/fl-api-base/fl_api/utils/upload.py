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

import os
import shutil
from pathlib import Path
from urllib.parse import urlparse

import requests
from fastapi import HTTPException

from fl_api.utils.constants import (
    META,
    SERVER_CHECKPOINT_ROOT_DEFAULT,
    SERVER_CHECKPOINT_ROOT_ENV,
    SERVER_CHECKPOINTS_PREFIX,
)
from fl_api.utils.io_utils import read_config
from fl_api.utils.logger import logger
from fl_api.utils.prepare_config import (
    configure_client,
    configure_config,
    configure_environment,
    configure_meta,
    configure_server,
    validate_config,
)
from fl_api.utils.schemas import FLAggregators, TrainingRound, UploadAppRequest
from fl_api.utils.validation import safe_join, validate_bundle_url

# De-bundled evaluation checkpoints are large (hundreds of MB); allow far more time than the
# code-file default used for the rest of the bundle.
_SERVER_CHECKPOINT_DOWNLOAD_TIMEOUT_SECONDS = 900


def _server_checkpoint_root() -> Path:
    """Root of the shared volume where server-only checkpoints are staged for the fl-server."""
    return Path(os.getenv(SERVER_CHECKPOINT_ROOT_ENV, SERVER_CHECKPOINT_ROOT_DEFAULT))


def _stage_server_checkpoint(url: str, model_id: str, file_name: str) -> None:
    """Download a de-bundled evaluation checkpoint to the shared server-checkpoint volume.

    Written to ``<SERVER_CHECKPOINT_ROOT>/<model_id>/<file_name>`` — outside the NVFLARE job dir —
    so it is never part of the submitted job and never deployed to clients. The fl-server mounts
    the same volume and loads the checkpoint from disk. Mirrors the Flower backend's /app/src mount.

    Assumes the shared volume is actually mounted at ``SERVER_CHECKPOINT_ROOT`` — there is no
    mount-presence check, so without one the ``mkdir`` below lands on the container's own disk and
    the fl-server never sees the checkpoint (its locators log "not found" and skip the model). On
    AWS this combination cannot occur: ``enable_efs`` gates the whole fl-api task definition, env
    var and volume together (see ``deploy/providers/AWS/variables.tf``).
    """
    dest_dir = safe_join(_server_checkpoint_root(), model_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = safe_join(dest_dir, file_name)

    # Stream the download to disk in chunks. Evaluation checkpoints are large
    # (the Ark+ weights are ~759 MiB), so buffering the whole body in memory
    # (resp.content) OOM-killed the fl-api container on Fargate (FLIP#695); an
    # 8 MiB chunk keeps peak memory flat regardless of checkpoint size.
    with requests.get(
        url, timeout=_SERVER_CHECKPOINT_DOWNLOAD_TIMEOUT_SECONDS, allow_redirects=False, stream=True
    ) as resp:
        # Mirror the SSRF/redirect guard used for the rest of the bundle: a 3xx would dodge
        # validate_bundle_url (which only saw the original URL), and raise_for_status() stops an
        # S3 error body from being written out as the checkpoint.
        if resp.is_redirect or resp.is_permanent_redirect:
            raise HTTPException(status_code=400, detail=f"Bundle URL returned a redirect: {url!r}.")
        resp.raise_for_status()
        with open(dest_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=8 * 1024 * 1024):
                if chunk:
                    fh.write(chunk)
    logger.info(f"Staged server-side checkpoint at {dest_path}")


def _infer_app_folder_name(model_id: str, s3_file_dir: str) -> str:
    """
    Derive app folder name:
      - If path contains '/<model_id>/.../app/<suffix>/...' -> 'app<suffix>'
      - Else -> 'app'
    """
    s3_file_dir_model = s3_file_dir.split(model_id)[-1]
    if "/app" not in s3_file_dir_model:
        return "app"

    suffix = s3_file_dir_model.split("/app")[-1].split("/")[0]  # '' or '/site-1'
    return f"app{suffix}"  # e.g. 'app' or 'app/site-1' -> 'app' or 'app/site-1'? (see note below)


def _relative_dir_for_download(model_id: str, s3_file_dir: str, file_name: str) -> tuple[str, str]:
    """
    Returns (app_folder_name, relative_dir) where relative_dir is relative to job_dir.

    For example, if the file is config_fed_client.json and is located in a path containing
    "/app/site-1/config_fed_client.json", then this function will return

        ("app_site-1", "app_site-1/config_fed_client.json")

    so that the file will be downloaded to <job_dir>/app_site-1/config_fed_client.json.

    Args:
        model_id (str): the model ID, used to identify the app folder in the S3 path.
        s3_file_dir (str): the directory of the file in S3, used to determine the app folder and relative path for
        download.
        file_name (str): the name of the file being downloaded, used to determine if it's the META file which should be
        placed in the root of the job directory.

    Returns:
        tuple[str, str]: a tuple containing the app folder name and the relative directory for download.
    """
    if file_name == META:
        # meta.json always goes in the job root
        return "app", ""

    app_folder_name = _infer_app_folder_name(model_id, s3_file_dir)

    if "/custom" in s3_file_dir:
        # Preserve nested custom structure under app_folder_name/custom/...
        parts = s3_file_dir.split("/custom", 1)
        return app_folder_name, f"{app_folder_name}/custom{parts[1]}"

    if "/config" in s3_file_dir:
        # Preserve nested config structure under app_folder_name/config/...
        parts = s3_file_dir.split("/config", 1)
        return app_folder_name, f"{app_folder_name}/config{parts[1]}"

    # Fallback: dump into app/custom
    return app_folder_name, "app/custom"


def upload_application(model_id: str, body: UploadAppRequest, upload_dir: str) -> dict[str, str]:
    """Uploads an application to the upload dir folder of the server.

    Downloads the files from the provided bundle_urls (AWS S3 pre-signed URLs) in the UploadAppRequest body, creates the
    necessary folder structure, and updates the configuration files accordingly.

    Args:
        model_id (str): id of the model.
        body (UploadAppRequest): UploadAppRequest object with info such as project_id, cohort_query, trusts
        upload_dir (str): directory where the application will be uploaded (session's upload dir.)
    Raises:
        HTTPException: if the application fails to upload, an error is raised.
        FileNotFoundError: if the application path is not found, an error is raised.
    Returns:
        dict[str, str]: a dictionary containing a success message and the path where the application was uploaded.

    .. code-block:: text

        <cwd>/
        └─ <upload_dir>/                    # passed in as upload_dir (session upload dir)
        └─ <model_id>/                      # job_dir
            ├─ meta.json                    # REQUIRED if >1 app folder (app + app_siteX etc)
            │                               # Written automatically only for the "app" folder.
            │                               # If you upload multiple app folders, you must include this at root.
            │
            ├─ app/                         # default application folder (always created)
            │  ├─ config/                   # NVFLARE config folder (expected by configure_* funcs)
            │  │  ├─ config_fed_server.json # expected/validated by configure_server()
            │  │  ├─ config_fed_client.json # expected/validated by configure_client()
            │  │  ├─ config.json            # updated by configure_config() (rounds overrides)
            │  │  └─ environment.json       # optional; updated by configure_environment() if present
            │  └─ custom/                   # "payload" code/data copied from bundle URLs
            │     └─ ...                    # any files / nested dirs from uploaded bundle
            │
            ├─ app_site-1/                  # OPTIONAL extra app folders (name derived from S3 path after "/app")
            │  ├─ config/
            │  │  ├─ config_fed_server.json
            │  │  ├─ config_fed_client.json
            │  │  ├─ config.json
            │  │  └─ environment.json (optional)
            │  └─ custom/
            │     └─ ...
            │
            ├─ app_site-2/                  # OPTIONAL (more per-site variants)
            │  └─ ...
            │
            └─ (note)                       # Any file not matched to "/config" or "/custom" in the URL path
                                            # is currently placed under:
                                            #   <model_id>/app/custom/<file>
                                            # (the fallback path)
    """
    logger.info(f"Received request to upload app: {model_id}")

    # model_id is the Central Hub model UUID (validated as a UUID by the endpoint); it names
    # the per-model job dir. This section copies every uploaded file into that dir.

    bundle_urls = body.bundle_urls  # Retrieve the files that the user has uploaded to the platform.

    # We create the job app in the upload dir folder
    job_dir = safe_join(Path.cwd() / upload_dir, model_id)

    # If the job directory already exists, we remove it to avoid conflicts with previous uploads.
    if job_dir.exists():
        logger.warning(f"Job directory {job_dir} already exists, removing it...")
        shutil.rmtree(job_dir, ignore_errors=True)

    # Clear any previously-staged server-side checkpoints for this model on the shared volume.
    checkpoint_dir = safe_join(_server_checkpoint_root(), model_id)
    if checkpoint_dir.exists():
        logger.warning(f"Server-checkpoint directory {checkpoint_dir} already exists, removing it...")
        shutil.rmtree(checkpoint_dir, ignore_errors=True)

    # Create default app folder (some bundles may only populate 'app')
    (job_dir / "app").mkdir(parents=True, exist_ok=True)

    logger.info(f"Downloading {len(bundle_urls)} files into job directory: {job_dir}")

    # Initialise list to keep track of app folder names
    app_folder_names: set[str] = set()

    for url in bundle_urls:
        logger.info(f"Downloading file from {url}")

        # The FL API fetches each URL server-side, so reject non-https / off-origin URLs.
        validate_bundle_url(url)

        path = urlparse(url).path
        s3_file_dir, file_name = str(Path(path).parent), Path(path).name

        # Server-only evaluation checkpoints are staged on the shared volume for the fl-server
        # to load from disk; they are NOT part of the NVFLARE job (never deployed to clients).
        if f"/{SERVER_CHECKPOINTS_PREFIX}" in s3_file_dir:
            _stage_server_checkpoint(url, model_id, file_name)
            continue

        try:
            resp = requests.get(url, timeout=60, allow_redirects=False)
            # A redirect would dodge validate_bundle_url (which only saw the original URL),
            # reopening the SSRF hole; treat any 3xx as a failure. raise_for_status() also
            # stops an S3 4xx/5xx error body from being written out as the bundle file.
            if resp.is_redirect or resp.is_permanent_redirect:
                raise HTTPException(status_code=400, detail=f"Bundle URL returned a redirect: {url!r}.")
            resp.raise_for_status()
            content = resp.content
        except Exception as e:
            logger.error(f"Failed to download from URL {url} with error: {e}")
            raise

        app_folder_name, relative_dir = _relative_dir_for_download(model_id, s3_file_dir, file_name)
        app_folder_names.add(app_folder_name)

        # relative_dir + file_name derive from the (untrusted) S3 URL path; contain both
        # under job_dir so a crafted URL can't escape the upload sandbox.
        dest_dir = safe_join(job_dir, relative_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)

        dest_path = safe_join(dest_dir, file_name)
        dest_path.write_bytes(content)
        logger.info(f"Downloaded file {dest_path}")

    logger.info("Successfully downloaded application, updating application config...")

    # Require meta.json if more than one app folder is present
    logger.debug(f"App folder names identified: {app_folder_names}")
    if len(app_folder_names) > 1:
        meta_json_path = safe_join(job_dir, META)
        if not meta_json_path.exists():
            error_message = (
                f"Application must contain a {META} file in the root of the application folder if you have"
                " multiple app sub-folders indicating different behaviors per site."
            )
            logger.error(error_message)
            raise FileNotFoundError(error_message)

    # A federated learning job consists of, at least, a CONFIG and CUSTOM folder. Within CONFIG, two files,
    # config_fed_server.json and config_fed_client.json. Within custom, all uploaded files. In addition to
    # CONFIG and CUSTOM, a meta.json file has all the info concerning number of GPUs etc.
    # See more information in https://nvflare.readthedocs.io/en/2.6/real_world_fl/job.html#job

    # We run this configuration script for all of the different app folders.

    for app_folder_name in sorted(app_folder_names):
        logger.info(f"Configuring application folder: {app_folder_name}")
        app_folder_path = safe_join(job_dir, app_folder_name)

        # Grab config values from the uploaded config.json
        config_path = app_folder_path / "custom" / "config.json"
        raw_config = read_config(config_path)
        config = validate_config(raw_config)

        # Set defaults for config values if not provided in the uploaded config.json
        local_rounds = config.LOCAL_ROUNDS if config.LOCAL_ROUNDS else TrainingRound.MIN
        global_rounds = config.GLOBAL_ROUNDS if config.GLOBAL_ROUNDS else TrainingRound.MIN
        aggregator = config.AGGREGATOR if config.AGGREGATOR else FLAggregators.InTimeAccumulateWeightedAggregator.value
        aggregation_weights = config.AGGREGATION_WEIGHTS if config.AGGREGATION_WEIGHTS else {}
        ignore_result_error = config.IGNORE_RESULT_ERROR if config.IGNORE_RESULT_ERROR else False

        try:
            # Update config.json with the new global and local rounds
            configure_config(
                app_folder_path,
                global_rounds_override=global_rounds,
                local_rounds_override=local_rounds,
            )

            # Check if config_fed_client.json exists and verify its config.
            configure_client(
                app_folder_path,
                model_id,
                body.project_id,
                body.cohort_query,
                aggregate_only_regex=config.AGGREGATE_ONLY_REGEX,
            )

            # Check if config_fed_server.json exists and verify its config.
            configure_server(
                app_folder_path,
                model_id,
                global_rounds,  # TODO review this one
                body.trusts,
                ignore_result_error,
                aggregator,
                aggregation_weights,
                aggregate_only_regex=config.AGGREGATE_ONLY_REGEX,
                best_model_metric=config.BEST_MODEL_METRIC,
                best_model_metric_minimize=config.BEST_MODEL_METRIC_MINIMIZE,
            )

            # Configure the environment.json file if it exists.
            configure_environment(app_folder_path)

            # FIXME What if it's already there? Do we overwrite? For now, we overwrite without warning, but we could
            # consider merging if it already exists.
            if app_folder_name == "app":
                # Write meta.json file
                configure_meta(job_dir, model_id, body.trusts)

        except Exception as e:
            logger.error(f"Error occurred while configuring application folder {app_folder_name}: {e}")
            raise e

    response = {"message": f"Application uploaded successfully to: {job_dir}"}

    logger.info(response)

    return response
