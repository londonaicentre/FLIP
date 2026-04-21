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

"""Custom Federated Learning strategies for evaluation."""

from collections.abc import Iterable
from logging import INFO
from typing import Any, Dict, List, Tuple, Type

from flwr.common import MetricRecord, log
from flwr.common.message import Message
from flwr.serverapp.strategy import FedAvg


class MetricsValidator:
    """Validator for evaluation metrics returned from clients.

    Args:
        metrics_spec: Dictionary mapping metric names to their expected types.
                     Example: {"mean_dice": float, "raw_dice": list}
        model_names: List of model names to validate.
    """

    def __init__(self, metrics_spec: Dict[str, Type], model_names: List[str]):
        self.metrics_spec = metrics_spec
        self.model_names = model_names

    def validate(self, evaluation_results: Dict[str, Dict[str, Any]]) -> Tuple[bool, str]:
        """Validate that evaluation results match the expected structure and types.

        Args:
            evaluation_results: Dict mapping model names to their metrics.
                               Example: {"model1": {"mean_dice": 0.85, "raw_dice": [0.8, 0.9]}}

        Returns:
            Tuple of (is_valid, message)
        """
        # Check that all models in results are expected
        for model_name in evaluation_results.keys():
            if model_name not in self.model_names:
                return False, f"Model '{model_name}' is not in the list of expected models: {self.model_names}"

        # Validate each model's metrics
        for model_name, metrics in evaluation_results.items():
            if not isinstance(metrics, dict):
                return False, f"Metrics for model '{model_name}' must be a dictionary, got {type(metrics)}"

            # Check each metric against the spec
            for metric_name, metric_value in metrics.items():
                if metric_name not in self.metrics_spec:
                    return (
                        False,
                        f"Unexpected metric '{metric_name}' for model '{model_name}'. Expected metrics: {list(self.metrics_spec.keys())}",
                    )

                expected_type = self.metrics_spec[metric_name]
                success, message = self._validate_type(metric_name, metric_value, expected_type)
                if not success:
                    return False, f"Model '{model_name}': {message}"

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

    This strategy aggregates evaluation metrics from multiple clients across multiple models.

    Args:
        metrics_spec: Dictionary mapping metric names to their expected types.
        model_names: List of model names to evaluate.
    """

    def __init__(self, metrics_spec: Dict[str, Type], model_names: List[str], *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.metrics_spec = metrics_spec
        self.model_names = model_names
        self.validator = MetricsValidator(metrics_spec=metrics_spec, model_names=model_names)
        self.all_results: Dict[str, Dict[str, List]] = {}
        # Store per-client results: {model_name: {client_id: {metric_name: value}}}
        self.per_client_results: Dict[str, Dict[str, Dict[str, float]]] = {}

    def aggregate_evaluate(
        self,
        server_round: int,
        replies: Iterable[Message],
    ) -> MetricRecord | None:
        """Aggregate evaluation results from all clients."""

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
            log(INFO, f"DEBUG: Client {client_id} metrics keys: {list(metrics_outer.keys())}")

            # Extract the inner 'metrics' dict
            if "metrics" not in metrics_outer:
                log(INFO, f"Warning: No 'metrics' key from client {client_id}")
                continue

            metrics = metrics_outer["metrics"]
            log(INFO, f"DEBUG: Client {client_id} inner metrics: {metrics}")

            # Get client name from message config record (sent by client via SUPERNODE_NAME env var)
            config_outer = dict(msg.content.get("config", {}))
            client_name = config_outer.get("client_name", client_id)
            log(INFO, f"DEBUG: Client {client_id} using client_name: {client_name}")

            # Reconstruct evaluation results from flattened keys
            # Keys are in format: "evaluation.model_name.metric_name"
            client_eval = {}
            for key, value in metrics.items():
                if key.startswith("evaluation."):
                    # Extract model_name and metric_name from "evaluation.model_name.metric_name"
                    parts = key.split(".", 2)
                    if len(parts) == 3:
                        _, model_name, metric_name = parts
                        if model_name not in client_eval:
                            client_eval[model_name] = {}
                        client_eval[model_name][metric_name] = value

            if not client_eval:
                log(INFO, f"Warning: No evaluation results from client {client_name}")
                continue

            # Validate the evaluation results
            is_valid, message = self.validator.validate(client_eval)
            if not is_valid:
                log(INFO, f"Warning: Invalid evaluation results from client {client_name}: {message}")
                continue

            log(INFO, f"Client {client_name} - Valid evaluation results received")

            # Aggregate results
            for model_name, model_metrics in client_eval.items():
                if model_name not in self.all_results:
                    self.all_results[model_name] = {metric_name: [] for metric_name in self.metrics_spec.keys()}
                if model_name not in self.per_client_results:
                    self.per_client_results[model_name] = {}

                # Store per-client results using client name
                if client_name not in self.per_client_results[model_name]:
                    self.per_client_results[model_name][client_name] = {}

                # Collect metrics (only scalar numeric types: float or int)
                for metric_name in self.metrics_spec.keys():
                    if metric_name in model_metrics:
                        metric_value = model_metrics[metric_name]
                        # Convert to float for aggregation (works for both int and float types)
                        float_value = float(metric_value)
                        self.all_results[model_name][metric_name].append(float_value)
                        self.per_client_results[model_name][client_name][metric_name] = float_value

        # Calculate final aggregated results and return as MetricRecord
        aggregated_metrics = MetricRecord()

        # Add aggregated (mean) metrics
        for model_name, metrics_data in self.all_results.items():
            for metric_name, values in metrics_data.items():
                if not values:
                    continue

                # Calculate mean for all metrics (even if originally scalar)
                mean_value = sum(values) / len(values)
                # Ensure native Python types (not numpy)
                aggregated_metrics[f"{model_name}.{metric_name}_mean"] = float(mean_value)
                aggregated_metrics[f"{model_name}.{metric_name}_count"] = int(len(values))

        # Add per-client metrics
        for model_name, client_data in self.per_client_results.items():
            for client_name, metrics in client_data.items():
                for metric_name, value in metrics.items():
                    aggregated_metrics[f"{model_name}.{client_name}.{metric_name}"] = float(value)

        log(INFO, f"Round {server_round} - Aggregated evaluation results: {dict(aggregated_metrics)}")

        return aggregated_metrics if aggregated_metrics else None
