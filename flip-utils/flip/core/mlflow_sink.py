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
Best-effort MLflow dual-write sink (FLIP#745).

The hub (flip-api) stays canonical for model status and the metrics the FLIP UI
shows; this sink additionally mirrors metrics, status transitions and result
artefacts to an MLflow tracking server so researchers get cross-run comparison
and run -> weights lineage. It is deliberately never in the training critical
path: every public method swallows every exception, and the sink is disabled
entirely (``get_mlflow_sink()`` returns ``None``) when ``MLFLOW_TRACKING_URI``
is unset or the ``mlflow`` client library is unavailable.

Domain mapping (shared with flip-api's ``mlflow_run_service``):
    - FLIP model            -> MLflow experiment ``flip/<model_id>``
    - FL job (one training) -> MLflow run, resolved via the ``flip.model_id`` tag
    - per-trust metric      -> metric key ``<LABEL>/<fl_client_name>``, step = round
    - results zip           -> registered model version of ``flip-model-<model_id>``
      whose *source* is the existing S3 URI (a metadata reference — the weights
      are never copied into MLflow)

Run resolution: the fl-server never knows the hub's ``FLJob.id``, so the run
for a model is found by (1) an in-process cache, then (2) searching the
experiment for the newest RUNNING run tagged ``flip.model_id`` (this is how the
run pre-created by flip-api at job submit is adopted, and how a restarted
fl-server resumes), then (3) creating a fresh run. The platform's existing
one-live-job-per-model assumption makes the RUNNING run unambiguous.
"""

import logging
import os
import re
import threading
import time
from datetime import datetime, timezone

from flip.constants.flip_constants import FlipConstants, ModelStatus

logger = logging.getLogger("MlflowSink")

# Naming + tag conventions. flip-api's mlflow_run_service mirrors these — keep in sync.
EXPERIMENT_PREFIX = "flip"
REGISTERED_MODEL_PREFIX = "flip-model"
TAG_MODEL_ID = "flip.model_id"
TAG_NET_ID = "flip.net_id"
TAG_STATUS = "flip.status"
TAG_RESULTS_URI = "flip.results_uri"
TAG_LAST_ERROR = "flip.last_error"

# ModelStatus values that end an MLflow run, mapped to MLflow's RunStatus vocabulary.
_TERMINAL_STATUS_MAP = {
    ModelStatus.RESULTS_UPLOADED: "FINISHED",
    ModelStatus.ERROR: "FAILED",
    ModelStatus.RESULTS_UPLOAD_FAILED: "FAILED",
    ModelStatus.STOPPED: "KILLED",
}

# Keep a hung tracking server from stalling training-side event handlers: a dead
# MLflow costs seconds per call, not minutes. setdefault only — an operator's
# explicit values win.
_HTTP_ENV_DEFAULTS = {
    "MLFLOW_HTTP_REQUEST_TIMEOUT": "10",
    "MLFLOW_HTTP_REQUEST_MAX_RETRIES": "1",
}

# MLflow rejects metric keys outside its allowed charset; anything else becomes "_"
# so an exotic client name degrades the key instead of dropping the metric.
_INVALID_KEY_CHARS = re.compile(r"[^0-9A-Za-z_./\- ]")


def experiment_name(model_id: str) -> str:
    """Return the MLflow experiment name for a FLIP model.

    Args:
        model_id (str): The FLIP model identifier.

    Returns:
        str: The experiment name, ``flip/<model_id>``.
    """
    return f"{EXPERIMENT_PREFIX}/{model_id}"


def registered_model_name(model_id: str) -> str:
    """Return the MLflow registered-model name for a FLIP model.

    Args:
        model_id (str): The FLIP model identifier.

    Returns:
        str: The registered model name, ``flip-model-<model_id>``.
    """
    return f"{REGISTERED_MODEL_PREFIX}-{model_id}"


class MlflowSink:
    """Best-effort MLflow mirror. Every public method swallows every exception.

    Uses the low-level ``MlflowClient`` exclusively — never the fluent
    ``mlflow.start_run`` API — because callers are event handlers, not a linear
    script, and global active-run state would be a hazard.
    """

    def __init__(self, tracking_uri: str):
        import mlflow
        from mlflow.tracking import MlflowClient

        for key, value in _HTTP_ENV_DEFAULTS.items():
            os.environ.setdefault(key, value)

        if tracking_uri.startswith("arn:"):
            # SageMaker managed MLflow: the sagemaker-mlflow plugin's request-auth
            # provider resolves the App ARN from the GLOBAL tracking URI (the
            # MLFLOW_TRACKING_URI env var / mlflow.set_tracking_uri), not from the
            # per-client URI passed to MlflowClient below. Deployments deliver the
            # ARN via that env var, so this is normally a no-op — but it keeps the
            # sink correct if the URI ever arrives another way. Verified against a
            # live MLflow App (FLIP#745 WP6 spike).
            mlflow.set_tracking_uri(tracking_uri)

        self._client = MlflowClient(tracking_uri=tracking_uri, registry_uri=tracking_uri)
        self._run_ids: dict[str, str] = {}
        self._lock = threading.Lock()

    def log_metric(self, model_id: str, client_name: str, label: str, value: float, round: int) -> None:
        """Mirror one scalar metric to MLflow as ``<label>/<client_name>`` at step ``round``.

        Args:
            model_id (str): The FLIP model identifier.
            client_name (str): The FL client (kit slot) the metric is attributed to.
            label (str): The metric label (e.g. ``LOSS_FUNCTION``).
            value (float): The metric value.
            round (int): The global round, used as the MLflow step.
        """
        try:
            run_id = self._resolve_run_id(model_id)
            key = _INVALID_KEY_CHARS.sub("_", f"{label}/{client_name}")
            self._client.log_metric(run_id, key, value, timestamp=int(time.time() * 1000), step=round)
        except Exception as e:
            logger.warning(f"MLflow dual-write dropped metric {label} for model {model_id}: {e}")

    def on_status(self, model_id: str, status: ModelStatus) -> None:
        """Mirror a model status transition; terminal statuses also end the run.

        A terminal status never creates a run just to close it: if no live run
        exists (e.g. the hub already terminated it on abort), the event is
        dropped rather than leaving a status-only noise run behind.

        Args:
            model_id (str): The FLIP model identifier.
            status (ModelStatus): The status the model just transitioned to.
        """
        try:
            terminal = status in _TERMINAL_STATUS_MAP
            run_id = self._resolve_run_id(model_id, create=not terminal)
            if run_id is None:
                return
            self._client.set_tag(run_id, TAG_STATUS, status.value)
            if terminal:
                self._client.set_terminated(run_id, status=_TERMINAL_STATUS_MAP[status])
                with self._lock:
                    self._run_ids.pop(model_id, None)
        except Exception as e:
            logger.warning(f"MLflow dual-write dropped status {status} for model {model_id}: {e}")

    def note_exception(self, model_id: str, client_name: str, formatted_exception: str) -> None:
        """Record the latest handled client exception as a run tag.

        Args:
            model_id (str): The FLIP model identifier.
            client_name (str): The FL client that raised the exception.
            formatted_exception (str): The formatted exception text (truncated to 500 chars).
        """
        try:
            run_id = self._resolve_run_id(model_id)
            self._client.set_tag(run_id, TAG_LAST_ERROR, f"{client_name}: {formatted_exception}"[:500])
        except Exception as e:
            logger.warning(f"MLflow dual-write dropped exception note for model {model_id}: {e}")

    def register_results(self, model_id: str, results_uri: str) -> None:
        """Register an uploaded results zip as a new model version, by reference.

        The version's *source* is the existing S3 object written by
        ``upload_results_to_s3`` — no bytes are copied into MLflow, so the
        weights exist exactly once (downloads keep going through the hub's
        results endpoint).

        Args:
            model_id (str): The FLIP model identifier.
            results_uri (str): Fully-qualified S3 URI of the results zip.
        """
        try:
            run_id = self._resolve_run_id(model_id)
            self._client.set_tag(run_id, TAG_RESULTS_URI, results_uri)
            name = registered_model_name(model_id)
            try:
                self._client.create_registered_model(name)
            except Exception:
                pass  # already registered — versions accumulate under the same name
            version = self._client.create_model_version(name=name, source=results_uri, run_id=run_id)
            logger.info(f"Registered {name} v{version.version} -> {results_uri}")
        except Exception as e:
            logger.warning(f"MLflow dual-write could not register results for model {model_id}: {e}")

    def _resolve_run_id(self, model_id: str, create: bool = True) -> str | None:
        """Resolve the MLflow run for a model: cache -> newest RUNNING run -> create.

        Args:
            model_id (str): The FLIP model identifier.
            create (bool): Whether to create a fresh run when none is RUNNING.

        Returns:
            str | None: The MLflow run id, or ``None`` when nothing is RUNNING
            and ``create`` is False.
        """
        with self._lock:
            cached = self._run_ids.get(model_id)
        if cached is not None:
            return cached

        experiment_id = self._get_or_create_experiment(model_id)
        runs = self._client.search_runs(
            [experiment_id],
            filter_string=f"tags.{TAG_MODEL_ID} = '{model_id}' and attributes.status = 'RUNNING'",
            order_by=["attributes.start_time DESC"],
            max_results=1,
        )
        if runs:
            run_id = str(runs[0].info.run_id)
        elif create:
            run = self._client.create_run(
                experiment_id,
                tags={
                    TAG_MODEL_ID: model_id,
                    # DevSettings has no NET_ID — default rather than fail on the simulator path.
                    TAG_NET_ID: str(getattr(FlipConstants, "NET_ID", "")),
                },
                run_name=f"run-{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
            )
            run_id = str(run.info.run_id)
        else:
            return None

        with self._lock:
            self._run_ids[model_id] = run_id
        return run_id

    def _get_or_create_experiment(self, model_id: str) -> str:
        """Return the experiment id for a model, creating the experiment if needed.

        Args:
            model_id (str): The FLIP model identifier.

        Returns:
            str: The MLflow experiment id.
        """
        name = experiment_name(model_id)
        experiment = self._client.get_experiment_by_name(name)
        if experiment is not None:
            return str(experiment.experiment_id)
        try:
            return str(self._client.create_experiment(name))
        except Exception:
            # Lost a create race — someone else (e.g. flip-api) made it first.
            experiment = self._client.get_experiment_by_name(name)
            if experiment is None:
                raise
            return str(experiment.experiment_id)


_sink: MlflowSink | None = None
_sink_initialised = False
_sink_lock = threading.Lock()


def get_mlflow_sink() -> MlflowSink | None:
    """Return the process-wide sink, or ``None`` when the dual-write is disabled.

    Disabled means ``FlipConstants.MLFLOW_TRACKING_URI`` is empty (the default)
    or the ``mlflow`` client library cannot be imported. The decision is made
    once per process; call :func:`reset_mlflow_sink` to re-evaluate (tests).

    Returns:
        MlflowSink | None: The singleton sink, or ``None`` when disabled.
    """
    global _sink, _sink_initialised
    with _sink_lock:
        if not _sink_initialised:
            _sink_initialised = True
            tracking_uri = str(getattr(FlipConstants, "MLFLOW_TRACKING_URI", "") or "")
            if tracking_uri:
                try:
                    _sink = MlflowSink(tracking_uri)
                    logger.info(f"MLflow dual-write enabled -> {tracking_uri}")
                except Exception as e:
                    logger.warning(f"MLflow dual-write disabled (client init failed): {e}")
        return _sink


def reset_mlflow_sink() -> None:
    """Discard the singleton so the next :func:`get_mlflow_sink` re-evaluates the environment."""
    global _sink, _sink_initialised
    with _sink_lock:
        _sink = None
        _sink_initialised = False
