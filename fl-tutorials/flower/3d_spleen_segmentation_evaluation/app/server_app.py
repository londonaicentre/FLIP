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

"""quickstart-monai: A Flower / MONAI server app (evaluation-only)."""

import os
from typing import Dict, List, Optional, Tuple

import torch
from flip import FLIP
from flip.constants.flip_constants import ModelStatus
from flwr.app import ArrayRecord, Context
from flwr.common import EvaluateRes, Scalar
from flwr.serverapp import Grid, ServerApp
from flwr.serverapp.strategy import FedAvg

from app.models import get_model_for_path


def parse_models_config(run_config: Dict) -> Dict:
    """Reconstruct the models dict from Flower's flattened dot-separated run_config keys.

    Flower flattens ``[tool.flwr.app.config.models.foo]`` into keys like
    ``"models.foo.checkpoint"`` and ``"models.foo.path"`` in run_config.
    This function reconstructs the expected nested structure::

        {
            "foo": {"checkpoint": "...", "path": "..."}
        }
    """
    models: Dict[str, Dict] = {}
    prefix = "models."
    for key, value in run_config.items():
        if key.startswith(prefix):
            rest = key[len(prefix) :]  # e.g. "monai_spleen_unet.checkpoint"
            parts = rest.split(".", 1)
            if len(parts) == 2:
                model_name, field = parts
                models.setdefault(model_name, {})[field] = value
    return models


# Separator used to namespace per-model parameter keys inside a single ArrayRecord.
# Must not appear in any PyTorch state-dict key.
_MODEL_KEY_SEP = "/"


def pack_models(models: Dict[str, torch.nn.Module]) -> ArrayRecord:
    """Pack multiple models' state dicts into one ArrayRecord.

    Each tensor key is prefixed with ``<model_name><sep><original_key>``
    so that the client can extract per-model state dicts by filtering on the prefix.
    """
    combined: Dict[str, torch.Tensor] = {}
    for model_name, model in models.items():
        for layer_key, tensor in model.state_dict().items():
            combined[f"{model_name}{_MODEL_KEY_SEP}{layer_key}"] = tensor
    return ArrayRecord(combined)


class MetricsValidator:
    """Validator for evaluation metrics returned from clients."""

    def __init__(self, input_evaluation: Dict, input_models: List):
        self.input_evaluation = input_evaluation
        self.input_models = input_models

    def validate(self, input_evaluation: Dict) -> Tuple[bool, str]:
        """Validate that the evaluation results match the expected structure."""
        for model, evaluation in input_evaluation.items():
            if model not in self.input_models:
                return False, f"Model '{model}' is not in the list of input models."
            else:
                if not isinstance(evaluation, dict):
                    return False, "Each model must be mapped to a dictionary of metrics."
                success, message = self.validate_element(evaluation, self.input_evaluation)
                if not success:
                    return success, message
        return True, "Successfully validated all models."

    def validate_element(self, element: Dict, original_element: Dict) -> Tuple[bool, str]:
        """Recursively validate that metric values are of the correct type (float or list of floats)."""
        for key, value in element.items():
            if key not in original_element.keys():
                return False, f"Wrong metric '{key}' in evaluation."
            else:
                if isinstance(value, dict):
                    success, message = self.validate_element(value, original_element[key])
                    if not success:
                        return success, message
                else:
                    if not isinstance(value, (float, list)):
                        return False, f"Metric '{key}' must be a float or a list of floats."
                    if isinstance(value, list):
                        if not all(isinstance(x, (float, int)) for x in value):
                            return False, f"All elements in the list for metric '{key}' must be floats."
        return True, "Successfully validated elements."


class EvaluationStrategy(FedAvg):
    """Custom strategy for evaluation-only federated learning."""

    def __init__(self, evaluation_output: Dict, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.evaluation_output = evaluation_output
        self.validator = MetricsValidator(
            input_evaluation=evaluation_output, input_models=list(evaluation_output.keys())
        )
        self.all_results = {}

    def aggregate_evaluate(
        self,
        server_round: int,
        results: List[Tuple[str, EvaluateRes]],
        failures: List[Tuple[str, Exception]],
    ) -> Tuple[Optional[float], Dict[str, Scalar]]:
        """Aggregate evaluation results from all clients."""

        if not results:
            return None, {}

        # Process each client's evaluation results
        for client_id, evaluate_res in results:
            metrics = evaluate_res.metrics

            if "evaluation" in metrics:
                # Parse evaluation results (may need conversion from Scalar types)
                client_eval = self._parse_evaluation_metrics(metrics["evaluation"])

                # Validate the evaluation results
                is_valid, message = self.validator.validate(client_eval)
                if not is_valid:
                    print(f"Warning: Invalid evaluation results from client {client_id}: {message}")
                    continue

                # Aggregate results
                for model_name, model_metrics in client_eval.items():
                    if model_name not in self.all_results:
                        self.all_results[model_name] = {"mean_dice": [], "raw_dice": []}

                    # Collect mean_dice values
                    if "mean_dice" in model_metrics:
                        self.all_results[model_name]["mean_dice"].append(float(model_metrics["mean_dice"]))

                    # Collect raw_dice values
                    if "raw_dice" in model_metrics:
                        raw_dice = model_metrics["raw_dice"]
                        if isinstance(raw_dice, list):
                            self.all_results[model_name]["raw_dice"].extend([float(x) for x in raw_dice])

        # Calculate final aggregated results
        final_results = {}
        for model_name, results_data in self.all_results.items():
            raw_dice_values = results_data["raw_dice"]

            # Calculate overall mean dice
            overall_mean_dice = sum(raw_dice_values) / len(raw_dice_values) if raw_dice_values else 0.0

            final_results[model_name] = {"mean_dice": float(overall_mean_dice), "num_samples": len(raw_dice_values)}

        print(f"Round {server_round} - Aggregated evaluation results: {final_results}")

        # Return the mean dice averaged across all models as the top-level scalar
        all_mean_dice = [v["mean_dice"] for v in final_results.values() if "mean_dice" in v]
        aggregated_metric = sum(all_mean_dice) / len(all_mean_dice) if all_mean_dice else 0.0
        return aggregated_metric, final_results

    def _parse_evaluation_metrics(self, evaluation_data):
        """Parse evaluation metrics from Flower's Scalar types to Python types."""
        # This is a placeholder - the actual implementation depends on how
        # the metrics are serialized in the Message
        # For now, assume they are already in the correct format
        return evaluation_data


# Create ServerApp
app = ServerApp()


@app.main()
def main(grid: Grid, context: Context, flip: FLIP = FLIP()) -> None:
    """Main entry point for the ServerApp."""

    run_config = context.run_config
    num_rounds = int(run_config.get("num-server-rounds", 1))
    model_id = run_config.get("flip-model-id", "monai-flower-tutorial-model")

    flip.update_status(model_id, ModelStatus.INITIATED)

    # ------------------------------------------------------------------
    # Load all models listed in the run config.
    #
    # Expected config structure:
    #   "models": {
    #     "<model_name>": {
    #       "checkpoint": "<filename.pt>",   # relative to MODEL_CHECKPOINTS_DIR
    #       "path": "<architecture_key>"     # key in models.model_paths
    #     },
    #     ...
    #   }
    # ------------------------------------------------------------------
    models_config: Dict = parse_models_config(run_config)
    if not models_config:
        raise ValueError("No models specified. Set 'models' in pyproject.toml under [tool.flwr.app.config].")

    local_dev = os.getenv("LOCAL_DEV", "false").lower() == "true"
    if local_dev:
        checkpoints_dir = run_config.get("model-checkpoints")
        if not checkpoints_dir:
            raise ValueError("LOCAL_DEV is set but 'model-checkpoints' is not defined in pyproject.toml config.")
    else:
        checkpoints_dir = os.getenv("MODEL_CHECKPOINTS_DIR")
        if checkpoints_dir is None:
            raise ValueError("MODEL_CHECKPOINTS_DIR environment variable is not set")

    loaded_models: Dict[str, torch.nn.Module] = {}
    for model_name, model_cfg in models_config.items():
        arch_path = model_cfg["path"]
        checkpoint_file = os.path.join(checkpoints_dir, model_cfg["checkpoint"])

        if not os.path.exists(checkpoint_file):
            raise FileNotFoundError(f"Checkpoint for model '{model_name}' not found at: {checkpoint_file}")

        model = get_model_for_path(arch_path)
        checkpoint = torch.load(checkpoint_file, map_location="cpu")
        model.load_state_dict(checkpoint, strict=False)
        loaded_models[model_name] = model
        print(f"Loaded model '{model_name}' (arch='{arch_path}') from {checkpoint_file}")

    flip.update_status(model_id, ModelStatus.PREPARED)

    # Pack all models into a single ArrayRecord with namespaced keys.
    arrays = pack_models(loaded_models)

    # Build the evaluation output template from the model names so the
    # validator and aggregator know what to expect.
    evaluation_output = {model_name: {"mean_dice": 0.0, "raw_dice": []} for model_name in models_config.keys()}

    # Use custom evaluation strategy
    strategy = EvaluationStrategy(
        evaluation_output=evaluation_output,
        fraction_train=0.0,  # No training
        fraction_evaluate=1.0,  # All clients evaluate
    )

    strategy.start(
        grid=grid,
        initial_arrays=arrays,
        num_rounds=num_rounds,
    )

    flip.update_status(model_id, ModelStatus.TRAINING_STARTED)

    # Final results are now stored in strategy.all_results
    print(f"Final aggregated results: {strategy.all_results}")

    flip.update_status(model_id, ModelStatus.RESULTS_UPLOADED)
