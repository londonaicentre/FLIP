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

from logging import INFO
from typing import Any, Dict, List, Optional, Tuple, Type

from flwr.common import EvaluateRes, Scalar, log
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

        Args:
            metric_name: Name of the metric
            value: The actual value
            expected_type: The expected type (float, int, list, etc.)

        Returns:
            Tuple of (is_valid, message)
        """
        # Strings are explicitly not allowed
        if isinstance(value, str):
            return False, f"Metric '{metric_name}' cannot be a string. Numeric types only."

        # Check for list type
        if expected_type == list:
            if not isinstance(value, list):
                return False, f"Metric '{metric_name}' expected type 'list', got '{type(value).__name__}'"

            if len(value) == 0:
                return True, ""  # Empty lists are valid

            # All elements must be numeric (int or float), no strings
            for i, elem in enumerate(value):
                if isinstance(elem, str):
                    return False, f"Metric '{metric_name}' list element at index {i} cannot be a string"
                if not isinstance(elem, (int, float)):
                    return (
                        False,
                        f"Metric '{metric_name}' list element at index {i} must be numeric, got '{type(elem).__name__}'",
                    )

            return True, ""

        # Check for exact type match
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

    def aggregate_evaluate(
        self,
        server_round: int,
        results: List[Tuple[str, EvaluateRes]],
        failures: List[Tuple[str, Exception]],
    ) -> Tuple[Optional[float], Dict[str, Scalar]]:
        """Aggregate evaluation results from all clients."""

        if not results:
            log(INFO, "No evaluation results to aggregate")
            return None, {}

        if failures:
            log(INFO, f"Received {len(failures)} failures during evaluation")

        # Process each client's evaluation results
        for client_id, evaluate_res in results:
            metrics = evaluate_res.metrics

            if "evaluation" not in metrics:
                log(INFO, f"Warning: No evaluation results from client {client_id}")
                continue

            # Parse evaluation results
            client_eval = self._parse_evaluation_metrics(metrics["evaluation"])

            # Validate the evaluation results
            is_valid, message = self.validator.validate(client_eval)
            if not is_valid:
                log(INFO, f"Warning: Invalid evaluation results from client {client_id}: {message}")
                continue

            log(INFO, f"Client {client_id} - Valid evaluation results received")

            # Aggregate results
            for model_name, model_metrics in client_eval.items():
                if model_name not in self.all_results:
                    self.all_results[model_name] = {metric_name: [] for metric_name in self.metrics_spec.keys()}

                # Collect metrics
                for metric_name in self.metrics_spec.keys():
                    if metric_name in model_metrics:
                        metric_value = model_metrics[metric_name]

                        # Handle list metrics: extend the list
                        if self.metrics_spec[metric_name] == list:
                            if isinstance(metric_value, list):
                                self.all_results[model_name][metric_name].extend([float(x) for x in metric_value])
                        # Handle scalar metrics: append to list for averaging
                        else:
                            self.all_results[model_name][metric_name].append(float(metric_value))

        # Calculate final aggregated results
        final_results = {}
        for model_name, metrics_data in self.all_results.items():
            model_final = {}

            for metric_name, values in metrics_data.items():
                if not values:
                    continue

                # Calculate mean for all metrics (even if originally scalar)
                mean_value = sum(values) / len(values)
                model_final[f"{metric_name}_mean"] = float(mean_value)
                model_final[f"{metric_name}_count"] = len(values)

            final_results[model_name] = model_final

        log(INFO, f"Round {server_round} - Aggregated evaluation results: {final_results}")

        # Return the average of all mean values as the top-level scalar
        all_means = []
        for model_data in final_results.values():
            for key, value in model_data.items():
                if key.endswith("_mean") and isinstance(value, (int, float)):
                    all_means.append(value)

        aggregated_metric = sum(all_means) / len(all_means) if all_means else 0.0
        return float(aggregated_metric), final_results

    def _parse_evaluation_metrics(self, evaluation_data: Any) -> Dict[str, Dict[str, Any]]:
        """Parse evaluation metrics from Flower's Scalar types to Python types.

        Args:
            evaluation_data: Raw evaluation data from the client message

        Returns:
            Parsed evaluation dictionary
        """
        # If it's already a dict, return it
        if isinstance(evaluation_data, dict):
            return evaluation_data

        # Handle other serialization formats if needed
        return {}
