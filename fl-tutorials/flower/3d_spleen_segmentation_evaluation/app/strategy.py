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

"""Custom Federated Learning strategy for evaluation."""

from collections.abc import Iterable
from logging import INFO
from typing import Any, Dict, List, Tuple, Type

from flip import FLIP
from flip.flower.metrics import handle_client_exception, handle_client_metrics
from flwr.common import MetricRecord, log
from flwr.common.message import Message
from flwr.serverapp.strategy import FedAvg


class MetricsValidator:
    """Validator for evaluation metrics returned from clients.

    Args:
        metrics_spec: Dictionary mapping metric names to their expected types.
                      Example: {"mean_dice": float}
    """

    def __init__(self, metrics_spec: Dict[str, Type]):
        self.metrics_spec = metrics_spec

    def validate(self, metrics: Dict[str, Any]) -> Tuple[bool, str]:
        """Validate that a client's metrics match the expected names and types.

        Args:
            metrics: Dict mapping metric names to values. Example: {"mean_dice": 0.85}

        Returns:
            Tuple of (is_valid, message)
        """
        if not isinstance(metrics, dict):
            return False, f"Metrics must be a dictionary, got {type(metrics)}"

        for metric_name, metric_value in metrics.items():
            if metric_name not in self.metrics_spec:
                return (
                    False,
                    f"Unexpected metric '{metric_name}'. Expected metrics: {list(self.metrics_spec.keys())}",
                )

            expected_type = self.metrics_spec[metric_name]
            success, message = self._validate_type(metric_name, metric_value, expected_type)
            if not success:
                return False, message

        return True, "Successfully validated all evaluation results"

    def _validate_type(self, metric_name: str, value: Any, expected_type: Type) -> Tuple[bool, str]:
        """Validate that a metric value matches the expected type.

        Only numeric types (int, float) are supported.

        Args:
            metric_name: Name of the metric
            value: The actual value
            expected_type: The expected type (float or int)

        Returns:
            Tuple of (is_valid, message)
        """
        # Strings are explicitly not allowed
        if isinstance(value, str):
            return False, f"Metric '{metric_name}' cannot be a string. Numeric types only."

        # Check for exact type match (int or float)
        if not isinstance(value, expected_type):
            return (
                False,
                f"Metric '{metric_name}' expected type '{expected_type.__name__}', got '{type(value).__name__}'",
            )

        return True, ""


class EvaluationStrategy(FedAvg):
    """Custom strategy for evaluation-only federated learning.

    This strategy aggregates evaluation metrics for a single model across multiple clients.

    Args:
        metrics_spec: Dictionary mapping metric names to their expected types.
        flip: FLIP instance used by the fl-server to forward metrics to the Central Hub.
        model_id: FLIP model ID (UUID) for the current run.
    """

    def __init__(
        self,
        metrics_spec: Dict[str, Type],
        flip: FLIP,
        model_id: str,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.metrics_spec = metrics_spec
        self.flip = flip
        self.model_id = model_id
        self.validator = MetricsValidator(metrics_spec=metrics_spec)
        # Aggregated metric values across clients: {metric_name: [values]}
        self.all_results: Dict[str, List] = {}
        # Per-client results: {client_name: {metric_name: value}}
        self.per_client_results: Dict[str, Dict[str, float]] = {}

    def aggregate_evaluate(
        self,
        server_round: int,
        replies: Iterable[Message],
    ) -> MetricRecord | None:
        """Aggregate evaluation results from all clients."""

        replies = list(replies)

        # fl-clients never reach the Central Hub directly — forward everything server-side.
        for msg in replies:
            handle_client_metrics(msg, server_round, self.model_id, self.flip)
            handle_client_exception(msg, self.model_id, self.flip)

        # Use parent class helper to filter valid replies
        valid_replies, error_replies = self._check_and_log_replies(replies, is_train=False, validate=False)

        if not valid_replies:
            log(INFO, "No valid evaluation results to aggregate")
            return None

        # Process each client's evaluation results
        for msg in valid_replies:
            client_id = msg.metadata.src_node_id

            # Extract metrics from the message content
            if not msg.content.metric_records:
                log(INFO, f"Warning: No metrics from client {client_id}")
                continue

            # metric_records contains {'metrics': {actual_metrics_dict}}
            metrics_outer = dict(msg.content.metric_records)

            # Extract the inner 'metrics' dict
            if "metrics" not in metrics_outer:
                log(INFO, f"Warning: No 'metrics' key from client {client_id}")
                continue

            metrics = metrics_outer["metrics"]

            # Get client name from message config record (sent by client via SUPERNODE_NAME env var)
            config_outer = dict(msg.content.get("config", {}))
            client_name = config_outer.get("client_name", client_id)

            # Reconstruct this client's metrics from flattened "evaluation.<metric_name>" keys
            client_eval = {}
            for key, value in metrics.items():
                if key.startswith("evaluation."):
                    metric_name = key.split(".", 1)[1]
                    client_eval[metric_name] = value

            if not client_eval:
                log(INFO, f"Warning: No evaluation results from client {client_name}")
                continue

            # Validate the evaluation results
            is_valid, message = self.validator.validate(client_eval)
            if not is_valid:
                log(INFO, f"Warning: Invalid evaluation results from client {client_name}: {message}")
                continue

            log(INFO, f"Client {client_name} - Valid evaluation results received")

            # Aggregate results (only scalar numeric types: float or int)
            self.per_client_results.setdefault(client_name, {})
            for metric_name in self.metrics_spec.keys():
                if metric_name in client_eval:
                    # Convert to float for aggregation (works for both int and float types)
                    float_value = float(client_eval[metric_name])
                    self.all_results.setdefault(metric_name, []).append(float_value)
                    self.per_client_results[client_name][metric_name] = float_value

        # Calculate final aggregated results and return as MetricRecord
        aggregated_metrics = MetricRecord()

        # Add aggregated (mean) metrics
        for metric_name, values in self.all_results.items():
            if not values:
                continue
            mean_value = sum(values) / len(values)
            # Ensure native Python types (not numpy)
            aggregated_metrics[f"{metric_name}_mean"] = float(mean_value)
            aggregated_metrics[f"{metric_name}_count"] = int(len(values))

        # Add per-client metrics
        for client_name, metrics in self.per_client_results.items():
            for metric_name, value in metrics.items():
                aggregated_metrics[f"{client_name}.{metric_name}"] = float(value)

        log(INFO, f"Round {server_round} - Aggregated evaluation results: {dict(aggregated_metrics)}")

        return aggregated_metrics if aggregated_metrics else None
