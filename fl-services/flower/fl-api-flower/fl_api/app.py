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
import tempfile
import threading
from pathlib import Path
from typing import Any
from uuid import UUID

import grpc
from fastapi import FastAPI, HTTPException, Query, status
from grpc_health.v1.health_pb2 import HealthCheckRequest, HealthCheckResponse
from grpc_health.v1.health_pb2_grpc import HealthStub
from tomlkit import dumps, parse

from fl_api.schemas import (
    ClientInfoModel,
    FlowerSubmitRunCommandResponse,
    HealthResponse,
    JobMetadata,
    JobStatus,
    NodeRegistrationRequest,
    RunLogs,
    ServerInfoModel,
    UploadAppRequest,
    normalize_status,
)
from fl_api.utils.redaction import redact_secrets
from fl_api.utils.upload import upload_application
from fl_api.utils.validation import safe_join, validate_tutorial_folder_name

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

_node_mapping_lock = threading.Lock()
_node_trust_mapping: dict[str, str] = {}  # Flower node_id → trust name

# `flwr log --show` asks the SuperLink for the run's stored log and returns; its own gRPC
# deadline is 5s, so anything past this means the CLI itself is wedged (unreachable
# SuperLink, uvx resolving the package) rather than a slow run.
_RUN_LOG_COMMAND_TIMEOUT_SECONDS = 60
_DEFAULT_RUN_LOG_MAX_CHARS = 8000


def _get_src_root() -> Path:
    return Path(os.getenv("FLOWER_SRC_ROOT", "/app/src"))


def _get_superlink_health_address() -> str:
    return os.getenv("SUPERLINK_HEALTH_ADDRESS", "").strip()


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


def _get_run_log_max_chars() -> int:
    raw_value = os.getenv("FLOWER_RUN_LOG_MAX_CHARS", "").strip()
    if not raw_value:
        return _DEFAULT_RUN_LOG_MAX_CHARS
    try:
        max_chars = int(raw_value)
        if max_chars <= 0:
            raise ValueError("max chars must be positive")
        return max_chars
    except ValueError:
        logger.warning(
            "Invalid FLOWER_RUN_LOG_MAX_CHARS='%s'. Falling back to %d characters.",
            raw_value,
            _DEFAULT_RUN_LOG_MAX_CHARS,
        )
        return _DEFAULT_RUN_LOG_MAX_CHARS


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


def _run_flwr_command(
    command: list[str], cwd: Path, action_name: str, timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
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


def _resolve_job_dir(folder: str) -> Path:
    # Resolve <src_root>/<folder> with traversal containment and confirm it exists. The name
    # itself is validated by the caller (a UUID for production submit, a charset-guarded
    # folder name for tutorials) before we get here.
    job_dir = safe_join(_get_src_root(), folder)
    if not job_dir.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job folder path does not exist: {job_dir}",
        )
    return job_dir


def _validate_tutorial_folder(tutorial_name: str) -> Path:
    # Tutorial submit: pre-baked tutorial folders (e.g. "numpy", "xray_classification") are
    # submitted by name, not a UUID, so they get a charset/traversal guard instead.
    validate_tutorial_folder_name(tutorial_name)
    return _resolve_job_dir(tutorial_name)


def _get_federation_nodes(src_root: Path) -> list[dict[str, Any]]:
    """Query the SuperLink Control API for connected SuperNode statuses."""
    command = ["uvx", "flwr", "federation", "list", "--federation", "@none/default", "local", "--format", "json"]
    result = _run_flwr_command(command, src_root, "federation list")
    payload = _parse_flwr_payload(result, "federation list")

    federation = payload.get("federation", {})
    nodes = federation.get("nodes", [])
    if not isinstance(nodes, list):
        logger.warning("Federation list response does not contain a valid 'nodes' list.")
        return []
    return nodes


def _parse_runs_payload(payload: dict[str, Any]) -> list[JobMetadata]:
    runs = payload.get("runs")
    if not isinstance(runs, list):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Flower list response does not contain a valid 'runs' list.",
        )

    jobs: list[JobMetadata] = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        try:
            jobs.append(
                JobMetadata(
                    job_id=str(run["run-id"]),
                    status=normalize_status(run["status"]),
                )
            )
        except KeyError as err:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Flower list response contains a run missing the {err} field.",
            ) from err
    return jobs


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


@app.post(
    "/register_node",
    status_code=status.HTTP_200_OK,
)
def register_node(body: NodeRegistrationRequest) -> dict[str, str]:
    """Register a SuperNode's Flower node_id → trust name mapping.

    Called by each SuperNode at startup so that check_client_status can resolve
    Flower node IDs to human-readable trust names.
    """
    with _node_mapping_lock:
        _node_trust_mapping[body.node_id] = body.name
    logger.info("Registered node %s as '%s'", body.node_id, body.name)
    return {"status": "registered"}


@app.get(
    "/check_client_status",
    status_code=status.HTTP_200_OK,
    response_model=list[ClientInfoModel],
)
def check_client_status(
    targets: list[str] | None = Query(None),
) -> list[ClientInfoModel]:
    src_root = _get_src_root()
    nodes = _get_federation_nodes(src_root)

    # Build node_id → status from the federation list
    node_statuses: dict[str, str] = {}
    for node in nodes:
        nid = str(node.get("node_id", ""))
        if nid:
            node_statuses[nid] = node.get("status", "")

    # Build name → online using the in-memory node_id → trust_name mapping
    with _node_mapping_lock:
        mapping = dict(_node_trust_mapping)

    name_online: dict[str, bool] = {}
    for nid, name in mapping.items():
        name_online[name] = node_statuses.get(nid, "") == "online"

    # Log unmapped nodes for debugging
    mapped_ids = set(mapping.keys())
    for nid in node_statuses:
        if nid not in mapped_ids:
            logger.debug("Federation node %s has no registered trust name.", nid)

    # If targets are specified, filter; otherwise return all registered names
    target_names = targets if targets is not None else sorted(name_online.keys())

    result: list[ClientInfoModel] = []
    for name in target_names:
        is_connected = name_online.get(name, False)
        result.append(
            ClientInfoModel(
                name=name,
                status="CONNECTED" if is_connected else "DISCONNECTED",
            )
        )
    return result


@app.get("/list_runs", status_code=status.HTTP_200_OK, response_model=list[JobMetadata])
@app.get("/list_jobs", include_in_schema=False)  # alias, hide from docs
def list_runs() -> list[JobMetadata]:
    src_root = _get_src_root()
    command = ["uvx", "flwr", "list", "local", "--format", "json"]

    result = _run_flwr_command(command, src_root, "list")
    payload = _parse_flwr_payload(result, "list")
    return _parse_runs_payload(payload)


def _tail(text: str, max_chars: int) -> tuple[str, bool]:
    """Return the last ``max_chars`` characters of ``text`` and whether anything was dropped.

    Args:
        text (str): The full text.
        max_chars (int): Maximum number of characters to keep.

    Returns:
        tuple[str, bool]: The tail, and True when the head was dropped. The cut is
            advanced to the next line boundary so the tail never opens mid-line.
    """
    if len(text) <= max_chars:
        return text, False

    tail = text[-max_chars:]
    newline = tail.find("\n")
    if newline != -1:
        tail = tail[newline + 1 :]
    return tail, True


@app.get("/run_logs/{run_id}", status_code=status.HTTP_200_OK, response_model=RunLogs)
def run_logs(run_id: int) -> RunLogs:
    """Return a bounded, secret-masked tail of a run's ServerApp log.

    Exists so a run that dies after submission — an import error at ServerApp module
    scope, say — can be diagnosed from the Central Hub instead of by exec-ing into this
    container and running ``flwr log`` by hand (FLIP#1001). The hub's FL job reconcile
    calls this for a run it has found in a failed state and stores the result on the
    model's activity feed.

    Args:
        run_id (int): The Flower run id. Typed as ``int`` for the same reason as
            ``abort_run``: FastAPI rejects any non-numeric segment with 422 before it
            can reach the ``flwr`` argv.

    Returns:
        RunLogs: The run id, the log tail, and whether the head was dropped.

    Raises:
        HTTPException: 500 when the ``flwr log`` command cannot be run or fails.
    """
    run_id_str = str(run_id)
    # --show prints what the SuperLink has stored for the run and exits; the default
    # --stream would follow the log forever and never return to the caller.
    command = ["uvx", "flwr", "log", run_id_str, "local", "--show"]
    result = _run_flwr_command(command, _get_src_root(), "log", timeout=_RUN_LOG_COMMAND_TIMEOUT_SECONDS)

    if result.returncode != 0:
        stderr = result.stderr.strip()
        logger.error("Flower log failed for run %s: %s", run_id_str, stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Flower log command failed with code {result.returncode}. stderr: {redact_secrets(stderr)}",
        )

    # Redact before truncating, not after: the tail is cut from the middle of the log, and
    # a cut landing inside a `key=<secret>` pair would strip the keyword the matcher needs.
    log_tail, truncated = _tail(redact_secrets(result.stdout), _get_run_log_max_chars())
    return RunLogs(run_id=run_id_str, log=log_tail, truncated=truncated)


def _submit_from_job_dir(job_dir: Path, label: str) -> str:
    global _submission_in_progress

    # config.toml holds the run-config overrides for this app. It may sit on a
    # read-only bind mount, so rather than mutating it we merge flip-job-dir into a
    # temp copy and pass that to `flwr run` -- flwr also rejects mixing a config
    # file with inline key=value overrides, so everything goes through one file.
    config_toml_path = job_dir / "app" / "config.toml"

    with _state_lock:
        if _submission_in_progress:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Another Flower submission is in progress.",
            )
        _submission_in_progress = True

    run_config_path: str | None = None
    try:
        command = ["uvx", "flwr", "run", ".", "local", "--format", "json"]
        if config_toml_path.is_file():
            logger.info("Using config.toml overrides from %s for job submission.", job_dir)
            # flip-job-dir points the evaluation ServerApp at the app directory,
            # where the uploaded model checkpoint lives.
            run_config_doc = parse(config_toml_path.read_text())
            run_config_doc["flip-job-dir"] = str(config_toml_path.parent)
            fd, run_config_path = tempfile.mkstemp(suffix=".toml", prefix="flwr-run-config-")
            os.close(fd)
            Path(run_config_path).write_text(dumps(run_config_doc))
            command += ["--run-config", run_config_path]
        else:
            logger.warning("No config.toml found in %s. Using default configuration.", job_dir)

        try:
            result = _run_flwr_command(command, job_dir, "submit")
        except HTTPException as err:
            raise HTTPException(
                status_code=err.status_code,
                detail=f"Failed to submit job from folder {label}: {err.detail}",
            ) from err

        response_payload = _parse_flwr_payload(result, "submit")
        logger.info("Raw Flower submit response payload: %s", response_payload)

        # Check if Flower reported a failure
        if not response_payload.get("success", True):
            error_msg = response_payload.get("error-message") or response_payload.get(
                "error", "Unknown error from Flower"
            )
            logger.error("Flower run command failed: %s", error_msg)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Flower run failed: {error_msg}",
            )

        resp = FlowerSubmitRunCommandResponse.model_validate(response_payload)

        logger.info(
            "Submitted Flower job from '%s' using command: %s",
            label,
            " ".join(command),
        )
        return resp.run_id
    finally:
        with _state_lock:
            _submission_in_progress = False
        if run_config_path is not None:
            os.unlink(run_config_path)


@app.post("/submit_run/{job_folder}", status_code=status.HTTP_200_OK, response_model=str)
@app.post("/submit_job/{job_folder}", include_in_schema=False)  # alias flip-api calls with a UUID
def submit_run(job_folder: UUID) -> str:
    """Submit the uploaded application in ``job_folder`` to the Flower control plane.

    Args:
        job_folder (UUID): The Central Hub model_id. The uploaded application lives in
            ``FLOWER_SRC_ROOT/<model_id>`` (created by ``/upload_app/{model_id}``), so the
            submit "job folder" and the upload "model_id" are the same UUID — flip-api calls
            this via the ``/submit_job`` alias. FastAPI rejects any non-UUID path segment
            with 422 before the handler runs, so the folder name is guaranteed safe.

    Returns:
        str: The Flower run id of the submitted job.
    """
    return _submit_from_job_dir(_resolve_job_dir(str(job_folder)), str(job_folder))


@app.post("/submit_tutorial/{tutorial_name}", status_code=status.HTTP_200_OK, response_model=str)
def submit_tutorial(tutorial_name: str) -> str:
    # Tutorial path: pre-baked tutorial folders are submitted by name (not a UUID), e.g.
    # `numpy` / `xray_classification`. Charset/traversal-guarded, contained under the src root.
    return _submit_from_job_dir(_validate_tutorial_folder(tutorial_name), tutorial_name)


def _find_terminal_run(src_root: Path, run_id: str) -> JobMetadata | None:
    """Return the run's JobMetadata if it exists and is already terminal, else None.

    Used to make ``DELETE /abort_run`` idempotent: a failed ``flwr stop`` followed by a
    ``flwr list`` showing the run in a terminal state is treated as a successful no-op.
    """
    list_command = ["uvx", "flwr", "list", "local", "--format", "json"]
    list_result = _run_flwr_command(list_command, src_root, "list")
    if list_result.returncode != 0:
        return None
    try:
        payload = _extract_json_from_stdout(list_result.stdout)
    except ValueError:
        return None
    for run in payload.get("runs", []):
        if isinstance(run, dict) and str(run.get("run-id")) == run_id:
            try:
                metadata = JobMetadata(job_id=run_id, status=normalize_status(run["status"]))
            except KeyError:
                # Unlike _parse_runs_payload (which raises), this is a best-effort idempotency
                # probe: a malformed run means "can't confirm terminal" -> None -> 500 fallthrough.
                return None
            if metadata.status in (JobStatus.FINISHED, JobStatus.FAILED, JobStatus.STOPPED):
                return metadata
            return None
    return None


@app.delete("/abort_run/{run_id}", status_code=status.HTTP_200_OK, response_model=JobMetadata)
@app.delete("/abort_job/{run_id}", include_in_schema=False)  # alias, hide from docs
def abort_run(run_id: int) -> JobMetadata:
    # Flower run ids are integers (flwr Context.run_id: int). Typing the path param as int
    # makes FastAPI reject any non-numeric value with 422 before it can reach the `flwr
    # stop` argv, closing the command-line-injection surface; downstream code keeps using
    # the string form.
    src_root = _get_src_root()
    run_id_str = str(run_id)
    command = ["uvx", "flwr", "stop", run_id_str, "local", "--format", "json"]
    result = _run_flwr_command(command, src_root, "stop")

    if result.returncode == 0:
        # A successful `flwr stop` means the run is stopped. `flwr stop --format json`
        # emits {"success": true, "run-id": ...} with no status field, so the post-abort
        # status is unconditionally STOPPED — same as fl-api-base's /abort_job.
        return JobMetadata(job_id=run_id_str, status=JobStatus.STOPPED)

    # `flwr stop` failed: the run may already be terminal. Aborting an already-terminal
    # run must be idempotent — fall back to `flwr list` and return its terminal status.
    terminal = _find_terminal_run(src_root, run_id_str)
    if terminal is not None:
        return terminal

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"Failed to abort job {run_id_str}: {result.stderr.strip()}",
    )


@app.post("/upload_app/{model_id}", status_code=status.HTTP_200_OK)
def upload_app(model_id: UUID, body: UploadAppRequest) -> dict[str, str]:
    """
    Upload an application to the server.

    Args:
        model_id (UUID): The ID of the model to associate the application with.
        body (UploadAppRequest): The request body containing the application details.

    Returns:
        dict[str, str]: A dictionary containing the status of the upload.
    """
    upload_dir = _get_src_root()
    return upload_application(str(model_id), body, upload_dir=upload_dir)
