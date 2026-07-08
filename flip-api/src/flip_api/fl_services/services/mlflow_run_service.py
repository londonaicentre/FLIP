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

"""
Best-effort MLflow run bootstrap (FLIP#745).

At job submit the hub pre-creates the MLflow run with everything the fl-server
cannot know — the hub's FLJob id, project, trust roster, backend, job type and
the config.json hyperparameters — so the fl-server's dual-write sink (in the
flip package, ``flip/core/mlflow_sink.py``) adopts this run by its
``flip.model_id`` tag instead of lazily creating a bare one. Creating the run
*before* the job reaches the FL backend also removes any create-race with the
fl-server's first status event.

Everything here is best-effort: no scheduler run may fail because of MLflow,
so every public function swallows every exception. flip-api stays the
canonical store for model status and the metrics the UI shows.

Naming and tag conventions mirror the flip package's ``mlflow_sink`` module
(flip-utils is not a flip-api dependency, hence the duplication — keep in sync).
"""

import json
import logging
import os
from typing import TYPE_CHECKING
from uuid import UUID

from sqlmodel import Session

from flip_api.config import get_settings
from flip_api.db.models.main_models import FLJob, Model
from flip_api.domain.interfaces.fl import IOverridableConfig
from flip_api.domain.schemas.types import FLBackend
from flip_api.utils.s3_client import S3Client

if TYPE_CHECKING:
    from mlflow.tracking import MlflowClient

logger = logging.getLogger(__name__)

# Keep in sync with flip-utils/flip/core/mlflow_sink.py.
EXPERIMENT_PREFIX = "flip"
TAG_MODEL_ID = "flip.model_id"
TAG_MODEL_NAME = "flip.model_name"
TAG_PROJECT_ID = "flip.project_id"
TAG_FL_JOB_ID = "flip.fl_job_id"
TAG_FL_BACKEND_JOB_ID = "flip.fl_backend_job_id"
TAG_BACKEND = "flip.backend"
TAG_JOB_TYPE = "flip.job_type"
TAG_TRUSTS = "flip.trusts"

# Same bounded-blast-radius defaults as the flip package sink: a dead MLflow
# costs the scheduler seconds per submit, not minutes. setdefault only.
_HTTP_ENV_DEFAULTS = {
    "MLFLOW_HTTP_REQUEST_TIMEOUT": "10",
    "MLFLOW_HTTP_REQUEST_MAX_RETRIES": "1",
}


def _get_client() -> "MlflowClient | None":
    """Build an MlflowClient, or return None when the integration is disabled.

    Returns:
        MlflowClient | None: A client bound to ``Settings.MLFLOW_TRACKING_URI``,
        or ``None`` when the URI is empty or the client cannot be built.
    """
    tracking_uri = get_settings().MLFLOW_TRACKING_URI
    if not tracking_uri:
        return None
    try:
        from mlflow.tracking import MlflowClient

        for key, value in _HTTP_ENV_DEFAULTS.items():
            os.environ.setdefault(key, value)
        return MlflowClient(tracking_uri=tracking_uri, registry_uri=tracking_uri)
    except Exception as e:
        logger.warning(f"MLflow run bootstrap disabled (client init failed): {e}")
        return None


def _get_or_create_experiment(client: "MlflowClient", model_id: UUID) -> str:
    """Return the experiment id for ``flip/<model_id>``, creating it if needed.

    Args:
        client (MlflowClient): The tracking client.
        model_id (UUID): The FLIP model identifier.

    Returns:
        str: The MLflow experiment id.
    """
    name = f"{EXPERIMENT_PREFIX}/{model_id}"
    experiment = client.get_experiment_by_name(name)
    if experiment is not None:
        return str(experiment.experiment_id)
    try:
        return str(client.create_experiment(name))
    except Exception:
        # Lost a create race — e.g. the fl-server sink made it first.
        experiment = client.get_experiment_by_name(name)
        if experiment is None:
            raise
        return str(experiment.experiment_id)


def _running_run_ids(client: "MlflowClient", experiment_id: str, model_id: UUID) -> list[str]:
    """Return ids of RUNNING runs tagged with this model, newest first.

    Args:
        client (MlflowClient): The tracking client.
        experiment_id (str): The experiment to search.
        model_id (UUID): The FLIP model identifier.

    Returns:
        list[str]: Run ids, newest first.
    """
    runs = client.search_runs(
        [experiment_id],
        filter_string=f"tags.{TAG_MODEL_ID} = '{model_id}' and attributes.status = 'RUNNING'",
        order_by=["attributes.start_time DESC"],
    )
    return [str(run.info.run_id) for run in runs]


def _read_config_params(model_id: UUID) -> tuple[str, dict[str, str]]:
    """Read job_type and the overridable hyperparameters from the uploaded config.json.

    Mirrors the bundlers' config.json discovery (list the scanned-files prefix,
    fetch the object) but never raises — a missing or malformed config simply
    yields the defaults.

    Args:
        model_id (UUID): The FLIP model identifier.

    Returns:
        tuple[str, dict[str, str]]: ``(job_type, params)`` where params holds the
        ``IOverridableConfig`` fields present in the config, stringified.
    """
    job_type = "standard"
    params: dict[str, str] = {}
    try:
        s3 = S3Client()
        model_files = s3.list_objects(f"{get_settings().SCANNED_MODEL_FILES_BUCKET}/{model_id}")
        config_file = next((k for k in model_files if k.endswith("/config.json")), None)
        if config_file is None:
            return job_type, params
        input_config = json.loads(s3.get_object(config_file)["Body"].read() or "{}")
        job_type = input_config.get("job_type") or job_type
        for field in IOverridableConfig.model_fields:
            if input_config.get(field) is not None:
                value = input_config[field]
                params[field] = json.dumps(value) if isinstance(value, dict) else str(value)
    except Exception as e:
        logger.warning(f"MLflow run bootstrap could not read config.json for model {model_id}: {e}")
    return job_type, params


def start_run_for_job(
    model_id: UUID,
    fl_job_id: UUID,
    slot_names: list[str],
    fl_backend: FLBackend,
    session: Session,
) -> None:
    """Pre-create the MLflow run for a job about to be submitted to the FL backend.

    Terminates (as FAILED) any stale RUNNING run left for this model — e.g. by an
    fl-server that died without a terminal status — then creates the run named
    ``job-<fl_job_id>`` carrying the lineage tags and the config.json params.

    Args:
        model_id (UUID): The model being trained.
        fl_job_id (UUID): The hub's FLJob id for this run.
        slot_names (list[str]): FL kit slot names of the participating trusts
            (the identities the FL protocol — and hence the metric series — use).
        fl_backend (FLBackend): The backend the job is submitted to.
        session (Session): DB session, used to read the model's name/project.
    """
    client = _get_client()
    if client is None:
        return
    try:
        experiment_id = _get_or_create_experiment(client, model_id)

        for stale_run_id in _running_run_ids(client, experiment_id, model_id):
            logger.info(f"MLflow run bootstrap terminating stale RUNNING run {stale_run_id} for model {model_id}")
            client.set_terminated(stale_run_id, status="FAILED")

        model = session.get(Model, model_id)
        job_type, params = _read_config_params(model_id)

        tags = {
            TAG_MODEL_ID: str(model_id),
            TAG_FL_JOB_ID: str(fl_job_id),
            TAG_BACKEND: fl_backend.value,
            TAG_JOB_TYPE: job_type,
            TAG_TRUSTS: ",".join(slot_names),
        }
        if model is not None:
            tags[TAG_MODEL_NAME] = model.name
            tags[TAG_PROJECT_ID] = str(model.project_id)
            client.set_experiment_tag(experiment_id, TAG_MODEL_NAME, model.name)
            client.set_experiment_tag(experiment_id, TAG_PROJECT_ID, str(model.project_id))

        run = client.create_run(experiment_id, tags=tags, run_name=f"job-{fl_job_id}")
        for key, value in params.items():
            client.log_param(run.info.run_id, key, value)
        logger.info(f"MLflow run {run.info.run_id} created for model {model_id} (job {fl_job_id})")
    except Exception as e:
        logger.warning(f"MLflow run bootstrap failed for model {model_id}: {e}")


def record_backend_job_id(model_id: UUID, fl_job_id: UUID, session: Session) -> None:
    """Tag the model's RUNNING run with the FL backend's job id (known post-submit).

    Args:
        model_id (UUID): The model being trained.
        fl_job_id (UUID): The hub's FLJob id, used to read the persisted backend job id.
        session (Session): DB session.
    """
    client = _get_client()
    if client is None:
        return
    try:
        fl_job = session.get(FLJob, fl_job_id)
        if fl_job is None or not fl_job.fl_backend_job_id:
            return
        experiment_id = _get_or_create_experiment(client, model_id)
        for run_id in _running_run_ids(client, experiment_id, model_id)[:1]:
            client.set_tag(run_id, TAG_FL_BACKEND_JOB_ID, fl_job.fl_backend_job_id)
    except Exception as e:
        logger.warning(f"MLflow run bootstrap could not record backend job id for model {model_id}: {e}")


def fail_run(model_id: UUID) -> None:
    """Terminate the model's RUNNING run(s) as FAILED after a submit-path failure.

    The fl-server never saw the job in this case, so no terminal status will
    arrive through the dual-write sink — without this the pre-created run would
    linger RUNNING until the next submit's stale-run sweep.

    Args:
        model_id (UUID): The model whose submit failed.
    """
    client = _get_client()
    if client is None:
        return
    try:
        experiment_id = _get_or_create_experiment(client, model_id)
        for run_id in _running_run_ids(client, experiment_id, model_id):
            client.set_terminated(run_id, status="FAILED")
    except Exception as e:
        logger.warning(f"MLflow run bootstrap could not fail run for model {model_id}: {e}")
