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

"""Custom Federated Learning strategies."""

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
    """

    def __init__(self, flip: FLIP, model_id: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.flip = flip
        self.model_id = model_id
        self.num_rounds = None

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

    def aggregate_train(
        self,
        server_round: int,
        replies: Iterable[Message],
    ) -> ArrayRecord | None:
        """Aggregate training results while capturing per-client metrics."""
        replies = list(replies)

        # Store per-client training metrics before aggregation
        for msg in replies:
            # Forward to the Central Hub via fl-server (fl-clients have no hub credentials).
            handle_client_metrics(msg, server_round, self.model_id, self.flip)
            handle_client_exception(msg, self.model_id, self.flip)

            if not msg.has_error() and msg.content.get("metrics"):
                client_metrics = dict(msg.content["metrics"])

                site_name = f"unknown_{msg.metadata.src_node_id}"
                if msg.content.get("config") and "site" in msg.content["config"]:
                    site_name = msg.content["config"]["site"]

                # Add site name to metrics for output
                client_metrics["site"] = site_name

                # Store per-client metrics using site name as key
                if server_round not in per_client_train_metrics:
                    per_client_train_metrics[server_round] = {}
                per_client_train_metrics[server_round][site_name] = client_metrics

        # Call parent method for standard aggregation
        return super().aggregate_train(server_round, replies)

    def aggregate_evaluate(
        self,
        server_round: int,
        replies: Iterable[Message],
    ) -> MetricRecord | None:
        """Aggregate evaluation metrics while capturing per-client results."""
        replies = list(replies)

        # Store per-client metrics before aggregation
        for msg in replies:
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
                if server_round not in per_client_eval_metrics:
                    per_client_eval_metrics[server_round] = {}
                per_client_eval_metrics[server_round][site_name] = client_metrics

        # Call parent method for standard aggregation
        return super().aggregate_evaluate(server_round, replies)
