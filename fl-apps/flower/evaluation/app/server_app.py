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

import json
import os
from logging import ERROR, INFO
from pathlib import Path
from typing import Dict, Type

import torch
from app.models import get_model_for_path
from flip import FLIP
from flip.constants.flip_constants import ModelStatus
from flwr.app import ArrayRecord, Context
from flwr.common import log
from flwr.serverapp import Grid, ServerApp

from app.strategy import EvaluationStrategy


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


def parse_metrics_config(metrics_config: Dict[str, str]) -> Dict[str, Type]:
    """Parse metrics configuration from pyproject.toml.

    Converts string type names (\"float\", \"int\") to actual Python types.
    Only native numeric types are allowed - no lists or complex types.

    Args:
        metrics_config: Dictionary mapping metric names to type strings.

    Returns:
        Dictionary mapping metric names to Python type objects.

    Raises:
        ValueError: If an unsupported type string is encountered.

    """
    # Only allow native numeric types (float, int) - no lists or other types
    type_mapping = {
        "float": float,
        "int": int,
    }

    metrics_spec = {}
    for metric_name, type_str in metrics_config.items():
        if type_str not in type_mapping:
            msg = (
                f"Unsupported type '{type_str}' for metric '{metric_name}'. "
                f"Only native numeric types are allowed: {list(type_mapping.keys())}"
            )
            log(ERROR, msg)
            raise ValueError(msg)
        metrics_spec[metric_name] = type_mapping[type_str]

    return metrics_spec


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


# Create ServerApp
app = ServerApp()


@app.main()
def main(grid: Grid, context: Context, flip: FLIP = FLIP()) -> None:
    """Main entry point for the ServerApp."""

    run_config = context.run_config
    num_rounds = int(run_config.get("num-server-rounds", 1))
    model_id = run_config.get("flip-model-id", "monai-flower-evaluation-model")

    flip.update_status(model_id, ModelStatus.INITIATED)

    # ------------------------------------------------------------------
    # Load all models listed in the run config.
    #
    # Expected config structure:
    #   "models": {
    #     "<model_name>": {
    #       "checkpoint": "<filename.pt>",   # relative to MODEL_CHECKPOINTS
    #       "path": "<architecture_key>"     # key in models.model_paths
    #     },
    #     ...
    #   }
    # ------------------------------------------------------------------
    models_config: Dict = parse_models_config(run_config)
    if not models_config:
        msg = "No models specified. Set 'models' in pyproject.toml under [tool.flwr.app.config]."
        log(ERROR, msg)
        raise ValueError(msg)

    local_dev = os.getenv("LOCAL_DEV", "false").lower() == "true"
    if local_dev:
        checkpoints_dir = run_config.get("model-checkpoints")
        if not checkpoints_dir:
            msg = "LOCAL_DEV is set but 'model-checkpoints' is not defined in pyproject.toml config."
            log(ERROR, msg)
            raise ValueError(msg)
    else:
        checkpoints_dir = os.getenv("MODEL_CHECKPOINTS")
        if checkpoints_dir is None:
            msg = "MODEL_CHECKPOINTS environment variable is not set"
            log(ERROR, msg)
            raise ValueError(msg)

    loaded_models: Dict[str, torch.nn.Module] = {}
    for model_name, model_cfg in models_config.items():
        arch_path = model_cfg["path"]
        checkpoint_file = os.path.join(checkpoints_dir, model_cfg["checkpoint"])

        if not os.path.exists(checkpoint_file):
            msg = f"Checkpoint for model '{model_name}' not found at: {checkpoint_file}"
            log(ERROR, msg)
            raise FileNotFoundError(msg)

        model = get_model_for_path(arch_path)
        checkpoint = torch.load(checkpoint_file, map_location="cpu", weights_only=True)
        model.load_state_dict(checkpoint, strict=False)
        loaded_models[model_name] = model
        log(INFO, f"Loaded model '{model_name}' (arch='{arch_path}') from {checkpoint_file}")

    flip.update_status(model_id, ModelStatus.PREPARED)

    # Pack all models into a single ArrayRecord with namespaced keys.
    arrays = pack_models(loaded_models)

    # Parse metrics specification from config
    # TOML nested tables are flattened in run_config, so extract keys starting with "metrics."
    metrics_config = {
        key.split(".", 1)[1]: value for key, value in context.run_config.items() if key.startswith("metrics.")
    }
    if not metrics_config:
        msg = "No metrics configuration found in pyproject.toml. Please define [tool.flwr.app.config.metrics]."
        log(ERROR, msg)
        raise ValueError(msg)

    metrics_spec = parse_metrics_config(metrics_config)
    log(INFO, f"Metrics specification: {metrics_spec}")

    # Use custom evaluation strategy
    strategy = EvaluationStrategy(
        metrics_spec=metrics_spec,
        model_names=list(models_config.keys()),
        fraction_train=0.0,  # No training
        fraction_evaluate=1.0,  # All clients evaluate
    )

    _ = strategy.start(
        grid=grid,
        initial_arrays=arrays,
        num_rounds=num_rounds,
    )

    log(INFO, f"\n{'=' * 60}")
    log(INFO, "Evaluation complete!")
    log(INFO, f"{'=' * 60}")

    # Get output directory using WORKING_DIR environment variable
    working_dir = os.getenv("WORKING_DIR", "/app")
    output_dir = Path(f"{working_dir}/{model_id}/evaluation_outputs")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Prepare evaluation results
    evaluation_results = {
        "num_rounds": num_rounds,
        "models_evaluated": list(models_config.keys()),
        "metrics_spec": {k: v.__name__ for k, v in metrics_spec.items()},
        "results": strategy.per_client_results,
    }

    # Save evaluation results to JSON
    json_path = output_dir / "evaluation_results.json"
    try:
        with open(json_path, "w") as f:
            json.dump(evaluation_results, f, indent=2)
        log(INFO, "✓ Evaluation results saved to %s", json_path)
    except Exception as e:
        log(INFO, "Failed to save evaluation results: %s", str(e))
        flip.update_status(model_id, ModelStatus.ERROR)
        return

    try:
        flip.upload_results_to_s3(output_dir, model_id)
        flip.update_status(model_id, ModelStatus.RESULTS_UPLOADED)
    except Exception as e:
        log(INFO, "Failed to upload results to S3: %s", str(e))
        flip.update_status(model_id, ModelStatus.ERROR)
        return

    log(INFO, "\n✓ Evaluation complete. All outputs saved to %s", output_dir)
    log(INFO, "  - Results JSON: evaluation_results.json")
