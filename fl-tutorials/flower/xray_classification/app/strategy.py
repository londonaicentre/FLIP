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

from flip.flower.strategy import FlipFedAvg
from flwr.app import ArrayRecord, Message, MetricRecord
from flwr.common import ConfigRecord
from flwr.serverapp import Grid

# Dictionary to store per-client metrics
per_client_train_metrics: dict[int, dict[str, dict]] = {}
per_client_eval_metrics: dict[int, dict[str, dict]] = {}


def _record_per_client_metrics(msg: Message, server_round: int, store: dict[int, dict[str, dict]]) -> None:
    """Capture one client's metrics into the per-round store for output artifacts."""
    if msg.has_error() or not msg.content.get("metrics"):
        return

    client_metrics = dict(msg.content["metrics"])

    # Extract site name from config (not metrics, as MetricRecord only accepts numeric values)
    site_name = f"unknown_{msg.metadata.src_node_id}"
    if msg.content.get("config") and "site" in msg.content["config"]:
        site_name = msg.content["config"]["site"]

    # Add site name to metrics for output
    client_metrics["site"] = site_name

    # Store per-client metrics using site name as key
    store.setdefault(server_round, {})[site_name] = client_metrics


class FedAvgWithClientMetrics(FlipFedAvg):
    """FedAvg strategy that captures per-client train and evaluation metrics.

    All Central Hub telemetry (status, metric/exception forwarding, round
    events) is inherited from ``FlipFedAvg``, as is best-model selection. This
    subclass only adds the app-specific behaviour: capturing per-client metrics
    for output, and running evaluation on the final round only — unless
    best-model selection is on, which needs every round evaluated.
    """

    def configure_evaluate(
        self, server_round: int, arrays: ArrayRecord, config: ConfigRecord, grid: Grid
    ) -> Iterable[Message]:
        """Configure evaluation on the final round, or every round when selecting a best model."""
        # Best-model selection scores each round's aggregated model, so it needs
        # the evaluate phase every round; otherwise only the final round matters.
        if server_round != self.num_rounds and not self.best_model_selection_enabled:
            return []

        return super().configure_evaluate(server_round, arrays, config, grid)

    def aggregate_train(
        self,
        server_round: int,
        replies: Iterable[Message],
    ) -> ArrayRecord | None:
        """Aggregate training results while capturing per-client metrics."""
        replies = list(replies)

        # Store per-client training metrics before aggregation; hub forwarding
        # happens in FlipFedAvg.
        for msg in replies:
            _record_per_client_metrics(msg, server_round, per_client_train_metrics)

        # Call parent method for hub telemetry + standard aggregation
        return super().aggregate_train(server_round, replies)

    def aggregate_evaluate(
        self,
        server_round: int,
        replies: Iterable[Message],
    ) -> MetricRecord | None:
        """Aggregate evaluation metrics while capturing per-client results."""
        replies = list(replies)

        # Store per-client metrics before aggregation; hub forwarding happens
        # in FlipFedAvg.
        for msg in replies:
            _record_per_client_metrics(msg, server_round, per_client_eval_metrics)

        # Call parent method for hub telemetry + standard aggregation
        return super().aggregate_evaluate(server_round, replies)
