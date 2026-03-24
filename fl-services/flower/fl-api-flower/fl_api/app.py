# Copyright (c) 2026 Flower Labs GmbH
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

import json
import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Any

import grpc
from fastapi import FastAPI, HTTPException, Query, status
from grpc_health.v1.health_pb2 import HealthCheckRequest, HealthCheckResponse
from grpc_health.v1.health_pb2_grpc import HealthStub

from fl_api.schemas import (
    ClientInfoModel,
    FlowerCommandResponse,
    FlowerSubmitRunCommandResponse,
    HealthResponse,
    RunRecord,
    ServerInfoModel,
    UploadAppRequest,
)
from fl_api.utils.upload import upload_application

logger = logging.getLogger("uvicorn")

app = FastAPI(
    title="FLIP FL API (Flower)",
    description="FL API for Flower deployment runtime.",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


_state_lock = threading.Lock()
_submission_in_progress = False


def _get_allowed_job_folders() -> set[str]:
    raw_value = os.getenv("ALLOWED_JOB_FOLDERS", "numpy,3d_spleen_segmentation,3d_spleen_segmentation_evaluation")
    return {item.strip() for item in raw_value.split(",") if item.strip()}


def _get_src_root() -> Path:
    return Path(os.getenv("FLOWER_SRC_ROOT", "/app/src"))


def _get_superlink_health_address() -> str:
    return os.getenv("SUPERLINK_HEALTH_ADDRESS", "").strip()


def _get_supernode_health_addresses() -> dict[str, str]:
    raw_value = os.getenv("SUPERNODE_HEALTH_ADDRESSES", "")
    if not raw_value.strip():
        return {}

    addresses: dict[str, str] = {}
    for raw_entry in raw_value.split(","):
        entry = raw_entry.strip()
        if not entry:
            continue
        if "=" not in entry:
            logger.warning(
                "Ignoring invalid SUPERNODE_HEALTH_ADDRESSES entry '%s'. Expected format 'name=host:port'.",
                entry,
            )
            continue

        name, address = (part.strip() for part in entry.split("=", 1))
        if not name or not address:
            logger.warning(
                "Ignoring invalid SUPERNODE_HEALTH_ADDRESSES entry '%s'. Name and address are required.",
                entry,
            )
            continue

        addresses[name] = address

    return addresses


def _get_healthcheck_timeout_seconds() -> float:
    raw_value = os.getenv("FLOWER_HEALTHCHECK_TIMEOUT_SECONDS", "1.0").strip()
    try:
        timeout = float(raw_value)
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        return timeout
    except ValueError:
        logger.warning(
            "Invalid FLOWER_HEALTHCHECK_TIMEOUT_SECONDS='%s'. Falling back to 1.0 seconds.",
            raw_value,
        )
        return 1.0


def _check_health(address: str, timeout: float) -> bool:
    if not address:
        logger.warning("Health check address is empty.")
        return False

    try:
        with grpc.insecure_channel(address) as channel:
            stub = HealthStub(channel)
            response = stub.Check(HealthCheckRequest(), timeout=timeout)
        return bool(response.status == HealthCheckResponse.SERVING)
    except grpc.RpcError as err:
        logger.info("Health check failed for %s: %s", address, err)
        return False
    except Exception as err:  # pragma: no cover - defensive guard
        logger.warning("Unexpected error while checking health for %s: %s", address, err)
        return False


def _extract_json_from_stdout(stdout: str) -> dict[str, Any]:
    cleaned = stdout.strip()
    if not cleaned:
        raise ValueError("No output from Flower command.")

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end < 0 or end <= start:
            raise ValueError(f"Output does not contain a JSON object: {cleaned}") from None
        parsed = json.loads(cleaned[start : end + 1])

    if not isinstance(parsed, dict):
        raise ValueError(f"Expected JSON object, got: {type(parsed).__name__}")

    return parsed


def _run_flwr_command(command: list[str], cwd: Path, action_name: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as err:
        logger.exception("Failed to run Flower %s command", action_name)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to {action_name} jobs: {err}",
        ) from err


def _parse_flwr_payload(result: subprocess.CompletedProcess[str], action_name: str) -> dict[str, Any]:
    if result.returncode != 0:
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        logger.error("Flower %s failed: %s", action_name, stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"Flower {action_name} command failed with code {result.returncode}. stderr: {stderr} stdout: {stdout}"
            ),
        )

    try:
        return _extract_json_from_stdout(result.stdout)
    except Exception as err:
        logger.exception("Failed to parse Flower %s JSON output", action_name)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to parse Flower {action_name} JSON output: {err}",
        ) from err


def _validate_app_folder(app_folder: str) -> Path:
    # allowed_job_folders = _get_allowed_job_folders()
    # if app_folder not in allowed_job_folders:
    #     raise HTTPException(
    #         status_code=status.HTTP_400_BAD_REQUEST,
    #         detail=(f"Invalid app folder '{app_folder}'. Allowed values: {sorted(allowed_job_folders)}"),
    #     )

    job_dir = _get_src_root() / app_folder
    if not job_dir.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job folder path does not exist: {job_dir}",
        )

    return job_dir


def _parse_runs_payload(payload: dict[str, Any]) -> list[RunRecord]:
    runs = payload.get("runs")
    if not isinstance(runs, list):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Flower list response does not contain a valid 'runs' list.",
        )

    validated_runs: list[RunRecord] = []
    for run in runs:
        if isinstance(run, dict):
            validated_runs.append(RunRecord.model_validate(run))
    return validated_runs


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get(
    "/check_server_status",
    status_code=status.HTTP_200_OK,
    response_model=ServerInfoModel,
)
def check_server_status() -> ServerInfoModel:
    superlink_health_address = _get_superlink_health_address()
    timeout_seconds = _get_healthcheck_timeout_seconds()

    if not superlink_health_address:
        logger.warning("SUPERLINK_HEALTH_ADDRESS is not configured.")
        return ServerInfoModel(status="STOPPED")

    is_running = _check_health(superlink_health_address, timeout_seconds)
    return ServerInfoModel(status="RUNNING" if is_running else "STOPPED")


@app.get(
    "/check_client_status",
    status_code=status.HTTP_200_OK,
    response_model=list[ClientInfoModel],
)
def check_client_status(
    targets: list[str] | None = Query(None),
) -> list[ClientInfoModel]:
    supernode_health_addresses = _get_supernode_health_addresses()
    timeout_seconds = _get_healthcheck_timeout_seconds()

    target_names = targets if targets is not None else list(supernode_health_addresses.keys())

    result: list[ClientInfoModel] = []
    for name in target_names:
        address = supernode_health_addresses.get(name)
        if address is None:
            logger.warning(
                "Requested health status for unknown target '%s'. Marking as DISCONNECTED.",
                name,
            )
            result.append(ClientInfoModel(name=name, status="DISCONNECTED"))
            continue
        is_connected = bool(address) and _check_health(address, timeout_seconds)
        result.append(
            ClientInfoModel(
                name=name,
                status="CONNECTED" if is_connected else "DISCONNECTED",
            )
        )
    return result


@app.get("/list_runs", status_code=status.HTTP_200_OK, response_model=list[RunRecord])
@app.get("/list_jobs", include_in_schema=False)  # alias, hide from docs
def list_runs() -> list[RunRecord]:
    src_root = _get_src_root()
    command = ["uvx", "flwr", "list", "local", "--format", "json"]

    result = _run_flwr_command(command, src_root, "list")
    payload = _parse_flwr_payload(result, "list")
    return _parse_runs_payload(payload)


@app.post("/submit_run/{app_folder}", status_code=status.HTTP_200_OK, response_model=str)
@app.post("/submit_job/{app_folder}", include_in_schema=False)  # alias, hide from docs
def submit_run(app_folder: str) -> str:
    job_dir = _validate_app_folder(app_folder)

    global _submission_in_progress

    # Override app config with FLIP parameters and user-provided overrides before submission
    # --run-config </path/to/config.toml> allows us to specify a config file that can override the defaults
    config_toml_path = job_dir / "config.toml"

    if config_toml_path.is_file():
        logger.info("Using config.toml overrides from %s for job submission.", job_dir)
        command = ["uvx", "flwr", "run", ".", "local", "--format", "json", "--run-config", str(config_toml_path)]
    else:
        logger.warning("No config.toml found in %s. Using default configuration.", job_dir)
        command = ["uvx", "flwr", "run", ".", "local", "--format", "json"]

    with _state_lock:
        if _submission_in_progress:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Another Flower submission is in progress.",
            )
        _submission_in_progress = True

    try:
        try:
            result = _run_flwr_command(command, job_dir, "submit")
        except HTTPException as err:
            raise HTTPException(
                status_code=err.status_code,
                detail=f"Failed to submit job from folder {app_folder}: {err.detail}",
            ) from err

        response_payload = _parse_flwr_payload(result, "submit")
        logger.info("Raw Flower submit response payload: %s", response_payload)
        resp = FlowerSubmitRunCommandResponse.model_validate(response_payload)

        logger.info(
            "Submitted Flower job from '%s' using command: %s",
            app_folder,
            " ".join(command),
        )
        return resp.run_id
    finally:
        with _state_lock:
            _submission_in_progress = False


@app.delete("/abort_run/{run_id}", status_code=status.HTTP_200_OK, response_model=FlowerCommandResponse)
@app.delete("/abort_job/{run_id}", include_in_schema=False)  # alias, hide from docs
def abort_run(run_id: str) -> FlowerCommandResponse:
    src_root = _get_src_root()
    command = ["uvx", "flwr", "stop", run_id, "local", "--format", "json"]

    try:
        result = _run_flwr_command(command, src_root, "stop")
    except HTTPException as err:
        raise HTTPException(
            status_code=err.status_code,
            detail=f"Failed to abort job {run_id}: {err.detail}",
        ) from err

    payload = _parse_flwr_payload(result, "stop")
    return FlowerCommandResponse.model_validate(payload)


@app.post("/upload_app/{model_id}", status_code=status.HTTP_200_OK)
def upload_app(model_id: str, body: UploadAppRequest) -> dict[str, str]:
    """
    Upload an application to the server.

    Args:
        model_id (str): The ID of the model to associate the application with.
        body (UploadAppRequest): The request body containing the application details.

    Returns:
        dict[str, str]: A dictionary containing the status of the upload.
    """
    upload_dir = _get_src_root()
    return upload_application(model_id, body, upload_dir=upload_dir)
