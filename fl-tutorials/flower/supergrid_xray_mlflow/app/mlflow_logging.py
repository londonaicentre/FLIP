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

"""Self-contained MLflow logging for the SuperGrid spike (FLIP#744 x FLIP#745).

Unlike the flip-utils ``MlflowSink`` (env-var driven, unavailable on Flower's
managed ServerApp pods where flip-utils resolves from PyPI), this module reads
its entire configuration from the Flower run-config, sets up AWS credentials
for the SageMaker managed-MLflow SigV4 plugin, and — crucially — uploads the
trained model WEIGHTS as MLflow artifacts. That is the whole point of the
spike: SuperGrid has no artifact download (`flwr pull` is roadmapped), so the
tracker is the only way to get the global model off the pod.

Best-effort like the sink: every public method swallows exceptions with a loud
log line, so MLflow problems never kill training. The reachability probe result
is logged unmistakably because failures would otherwise be silent.
"""

import os
import tempfile
from logging import INFO, WARNING

from flwr.common import log


class MlflowSpikeLogger:
    """Run-config-driven MLflow writer for the SuperGrid ServerApp."""

    def __init__(self, tracking_uri: str, experiment: str, model_id: str):
        import mlflow  # deferred so a missing package disables, not crashes

        self._mlflow = mlflow
        self._client = None
        self._run_id = None
        mlflow.set_tracking_uri(tracking_uri)
        self._client = mlflow.MlflowClient(tracking_uri=tracking_uri)
        exp = self._client.get_experiment_by_name(experiment)
        exp_id = exp.experiment_id if exp else self._client.create_experiment(experiment)
        run = self._client.create_run(exp_id, run_name=f"supergrid-{model_id}", tags={"flip.model_id": model_id})
        self._run_id = run.info.run_id
        self.model_id = model_id
        log(INFO, "MLFLOW PROBE OK: tracking server reachable, run %s created in experiment %s", self._run_id, exp_id)

    def log_metrics(self, metrics: dict, step: int, prefix: str = "") -> None:
        """Log numeric metrics (best-effort); non-numeric values are skipped."""
        try:
            for key, value in metrics.items():
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                self._client.log_metric(self._run_id, f"{prefix}{key}", float(value), step=step)
        except Exception as e:  # noqa: BLE001 - never let MLflow kill training
            log(WARNING, "MLFLOW log_metrics failed (round %s): %s", step, e)

    def log_final_model(self, output_dir, registered_name: str) -> None:
        """Upload every file in output_dir as run artifacts and register the model version."""
        try:
            for entry in sorted(output_dir.iterdir()):
                if entry.is_file():
                    self._client.log_artifact(self._run_id, str(entry), artifact_path="model")
            uploaded = [p.name for p in output_dir.iterdir() if p.is_file()]
            log(INFO, "MLFLOW ARTIFACTS OK: uploaded %s to run %s", uploaded, self._run_id)
        except Exception as e:  # noqa: BLE001
            log(WARNING, "MLFLOW ARTIFACT UPLOAD FAILED — model weights are NOT in MLflow: %s", e)
            return
        try:
            try:
                self._client.create_registered_model(registered_name)
            except Exception:  # noqa: BLE001 - already exists
                pass
            version = self._client.create_model_version(
                name=registered_name, source=f"runs:/{self._run_id}/model", run_id=self._run_id
            )
            log(INFO, "MLFLOW MODEL REGISTERED: %s version %s", registered_name, version.version)
        except Exception as e:  # noqa: BLE001
            log(WARNING, "MLFLOW model registration failed (artifacts are still uploaded): %s", e)

    def finish(self, status: str = "FINISHED") -> None:
        try:
            self._client.set_terminated(self._run_id, status=status)
        except Exception as e:  # noqa: BLE001
            log(WARNING, "MLFLOW run termination failed: %s", e)


def setup_mlflow(run_config, model_id: str) -> MlflowSpikeLogger | None:
    """Build the logger from Flower run-config; returns None (loudly) when disabled or unreachable.

    AWS credentials arrive via run-config because a Flower-managed pod carries no
    FLIP environment. They are exported for boto3/SigV4 before mlflow is touched.
    """
    tracking_uri = str(run_config.get("mlflow-tracking-uri", "") or "")
    if not tracking_uri:
        log(INFO, "MLFLOW DISABLED: mlflow-tracking-uri not set in run-config")
        return None

    for cfg_key, env_key in (
        ("mlflow-aws-access-key-id", "AWS_ACCESS_KEY_ID"),
        ("mlflow-aws-secret-access-key", "AWS_SECRET_ACCESS_KEY"),
        ("mlflow-aws-session-token", "AWS_SESSION_TOKEN"),
        ("mlflow-aws-region", "AWS_DEFAULT_REGION"),
        # HTTP tracking servers behind basic auth (the tunnel fallback when the
        # pod's egress proxy blocks the SageMaker MLflow domain — observed 403).
        ("mlflow-tracking-username", "MLFLOW_TRACKING_USERNAME"),
        ("mlflow-tracking-password", "MLFLOW_TRACKING_PASSWORD"),
    ):
        value = str(run_config.get(cfg_key, "") or "")
        if value:
            os.environ[env_key] = value

    experiment = str(run_config.get("mlflow-experiment", "") or "supergrid-xray-mlflow")
    try:
        logger = MlflowSpikeLogger(tracking_uri, experiment, model_id)
    except Exception as e:  # noqa: BLE001
        log(WARNING, "MLFLOW PROBE FAILED: tracking server unreachable from this pod — %s: %s", type(e).__name__, e)
        return None

    # Egress sanity for the artifact plane. With a SageMaker App URI clients
    # upload artifacts DIRECTLY to S3 (the App hands out the location, it does
    # not proxy bytes) — the tracking API can be open while S3 is blocked,
    # silently stranding the weights. With an HTTP server the artifacts are
    # proxied over the same connection, so this probe validates that too.
    try:
        with tempfile.TemporaryDirectory() as td:
            probe = os.path.join(td, "egress_probe.txt")
            with open(probe, "w") as f:
                f.write("supergrid egress probe\n")
            logger._client.log_artifact(logger._run_id, probe, artifact_path="probe")
        log(INFO, "MLFLOW S3 ARTIFACT PROBE OK: artifact upload works from this pod")
    except Exception as e:  # noqa: BLE001
        log(WARNING, "MLFLOW S3 ARTIFACT PROBE FAILED: metrics will flow but weights will NOT — %s: %s",
            type(e).__name__, e)
    return logger
