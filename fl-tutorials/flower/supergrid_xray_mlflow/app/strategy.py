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

"""Custom Federated Learning strategies.

SuperGrid spike note: this is the pre-FlipFedAvg (FLIP#744-spike-proven) shape —
it must import only what PyPI flip-utils 0.1.8 provides, because Flower's
managed ServerApp pod resolves flip-utils from PyPI, not /opt/flip-utils.
The additions over the spike shape are the optional per-round MLflow hooks.
"""

from collections.abc import Iterable

from flip import FLIP
from flip.constants.flip_constants import ModelStatus
from flip.flower.metrics import handle_client_exception, handle_client_metrics
from flwr.app import ArrayRecord, Message, MetricRecord
from flwr.common import ConfigRecord
from flwr.serverapp import Grid
from flwr.serverapp.strategy import FedAvg

# Dictionary to store per-client metrics
per_client_train_metrics: dict[int, dict[str, dict]] = {}
per_client_eval_metrics: dict[int, dict[str, dict]] = {}


class FedAvgWithClientMetrics(FedAvg):
    """FedAvg strategy that captures per-client train and evaluation metrics.

    This strategy extends the standard FedAvg to collect and store individual
    client metrics during training and evaluation, in addition to the aggregated
    metrics. It also supports configuring evaluation to run only on the final round.
    When an MLflow logger is supplied, per-client and aggregated metrics are
    mirrored there live, keyed ``<metric>/<site>`` with step = server round —
    the same convention as flip-utils' MlflowSink.
    """

    def __init__(self, flip: FLIP, model_id: str, *args, mlflow_logger=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.flip = flip
        self.model_id = model_id
        self.num_rounds = None
        self.mlflow_logger = mlflow_logger

    def start(self, grid: Grid, initial_arrays: ArrayRecord, num_rounds: int = 3, **kwargs):
        """Override start to capture num_rounds for evaluation control."""
        self.num_rounds = num_rounds
        self.flip.update_status(self.model_id, ModelStatus.TRAINING_STARTED)
        return super().start(grid, initial_arrays, num_rounds, **kwargs)

    def configure_evaluate(
        self, server_round: int, arrays: ArrayRecord, config: ConfigRecord, grid: Grid
    ) -> Iterable[Message]:
        """Configure evaluation only on the final round."""
        # Only evaluate on the last round
        if server_round != self.num_rounds:
            return []

        # Call parent method for final round evaluation
        return super().configure_evaluate(server_round, arrays, config, grid)

    def _collect_client_metrics(
        self, server_round: int, replies: Iterable[Message], store: dict[int, dict[str, dict]]
    ) -> None:
        """Capture per-client metrics from replies into store, mirroring each to MLflow."""
        for msg in replies:
            # Forward to the Central Hub via fl-server (fl-clients have no hub credentials).
            handle_client_metrics(msg, server_round, self.model_id, self.flip)
            handle_client_exception(msg, self.model_id, self.flip)

            if not msg.has_error() and msg.content.get("metrics"):
                client_metrics = dict(msg.content["metrics"])

                # Extract site name from config (not metrics, as MetricRecord only accepts numeric values)
                site_name = f"unknown_{msg.metadata.src_node_id}"
                if msg.content.get("config") and "site" in msg.content["config"]:
                    site_name = msg.content["config"]["site"]

                # Add site name to metrics for output
                client_metrics["site"] = site_name

                # Store per-client metrics using site name as key
                if server_round not in store:
                    store[server_round] = {}
                store[server_round][site_name] = client_metrics

                if self.mlflow_logger is not None:
                    self.mlflow_logger.log_metrics(
                        {f"{key}/{site_name}": value for key, value in client_metrics.items()}, step=server_round
                    )

    def _mirror_aggregated(self, server_round: int, aggregated) -> None:
        """Mirror an aggregated MetricRecord to MLflow as <metric>/aggregated."""
        if self.mlflow_logger is not None and isinstance(aggregated, MetricRecord):
            self.mlflow_logger.log_metrics(
                {f"{key}/aggregated": value for key, value in dict(aggregated).items()}, step=server_round
            )

    def aggregate_train(
        self,
        server_round: int,
        replies: Iterable[Message],
    ):
        """Aggregate training results while capturing per-client metrics."""
        replies = list(replies)
        self._collect_client_metrics(server_round, replies, per_client_train_metrics)

        # Call parent method for standard aggregation
        result = super().aggregate_train(server_round, replies)
        if isinstance(result, tuple) and len(result) == 2:
            self._mirror_aggregated(server_round, result[1])
        return result

    def aggregate_evaluate(
        self,
        server_round: int,
        replies: Iterable[Message],
    ):
        """Aggregate evaluation metrics while capturing per-client results."""
        replies = list(replies)
        self._collect_client_metrics(server_round, replies, per_client_eval_metrics)

        # Call parent method for standard aggregation
        result = super().aggregate_evaluate(server_round, replies)
        self._mirror_aggregated(server_round, result if isinstance(result, MetricRecord) else None)
        return result
