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

"""quickstart-monai: A Flower / MONAI evaluation-only app."""

import os
from logging import INFO
from typing import Dict

import torch
from flwr.app import ArrayRecord, Context, Message, MetricRecord, RecordDict
from flwr.clientapp import ClientApp
from flwr.common import log
from flwr.common.record import ConfigRecord
from monai.data import DataLoader, Dataset

from app.data_loading import FLIP_BASE
from app.models import get_model_for_path
from app.task import evaluate_func
from app.transforms import get_val_transforms


def parse_models_config(run_config: Dict) -> Dict:
    """Reconstruct the models dict from Flower's flattened dot-separated run_config keys.

    Flower flattens ``[tool.flwr.app.config.models.foo]`` into keys like
    ``"models.foo.checkpoint"`` and ``"models.foo.path"`` in run_config.
    """
    models: Dict[str, Dict] = {}
    prefix = "models."
    for key, value in run_config.items():
        if key.startswith(prefix):
            rest = key[len(prefix) :]
            parts = rest.split(".", 1)
            if len(parts) == 2:
                model_name, field = parts
                models.setdefault(model_name, {})[field] = value
    return models


# Must match the separator used in server_app.pack_models.
_MODEL_KEY_SEP = "/"


def unpack_model(arrays: ArrayRecord, model_name: str, model: torch.nn.Module) -> torch.nn.Module:
    """Load a single model's weights from a namespaced ArrayRecord.

    Keys in ``arrays`` are expected to be ``<model_name><sep><layer_key>``.
    Only entries that start with ``model_name`` are extracted.
    """
    full_state: Dict[str, torch.Tensor] = arrays.to_torch_state_dict()
    prefix = f"{model_name}{_MODEL_KEY_SEP}"
    model_state = {key[len(prefix) :]: tensor for key, tensor in full_state.items() if key.startswith(prefix)}
    if not model_state:
        raise KeyError(
            f"No weights found for model '{model_name}' in ArrayRecord. "
            f"Available prefixes: {sorted({k.split(_MODEL_KEY_SEP)[0] for k in full_state})}"
        )
    model.load_state_dict(model_state, strict=False)
    return model


# Flower ClientApp
app = ClientApp()


@app.evaluate()
def evaluate(msg: Message, context: Context) -> Message:
    """Evaluate all models on local test data."""
    # Configure evaluation parameters
    run_config = context.run_config

    models_config: Dict = parse_models_config(run_config)
    if not models_config:
        raise ValueError("No models specified. Set 'models' in pyproject.toml under [tool.flwr.app.config].")

    # NOTE this needs to match the name of the trust in the central hub database
    client_name = os.getenv("SUPERNODE_NAME", "unknown_client")

    # Configure FLIP
    flip_utils = FLIP_BASE()
    flip_utils.project_id = run_config.get("flip-project-id", "monai-flower-tutorial")
    flip_utils.query = run_config.get("flip-cohort-query", "*")
    log(INFO, "Fetching FLIP dataframe using project_id=%s and query=%s", flip_utils.project_id, flip_utils.query)
    flip_utils.dataframe = flip_utils.flip.get_dataframe(project_id=flip_utils.project_id, query=flip_utils.query)
    log(INFO, f"FLIP dataframe has {len(flip_utils.dataframe)} rows.")

    # Setup device
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    log(INFO, "Evaluating on device: %s", device)

    # Evaluation-only: score every matched image/label pair in this client's
    # cohort, not a held-out fraction.
    test_datalist = flip_utils.get_test_data_list()
    dataset_test = Dataset(test_datalist, transform=get_val_transforms())
    test_loader = DataLoader(dataset_test, batch_size=1, shuffle=False)

    # Arrays record contains weights for all models, namespaced by model name
    arrays: ArrayRecord = msg.content["arrays"]

    # Evaluate each model
    evaluation_results: Dict = {}
    for model_name, model_cfg in models_config.items():
        log(INFO, f"Evaluating model '{model_name}' (arch='{model_cfg['path']}')")

        model = get_model_for_path(model_cfg["path"])
        model = unpack_model(arrays, model_name, model)
        model.to(device)

        # evaluate_func sweeps the whole test_loader once and returns one dice
        # score per subject; with deterministic eval transforms and model.eval(),
        # repeating the sweep would produce identical numbers.
        dice_scores = evaluate_func(
            model=model,
            test_loader=test_loader,
            device=device,
        )

        overall_mean_dice = sum(dice_scores) / len(dice_scores) if dice_scores else 0.0
        evaluation_results[model_name] = {
            "mean_dice": float(overall_mean_dice),
        }
        log(INFO, f"  Model '{model_name}' mean dice: {overall_mean_dice:.4f}")

    # Flatten evaluation results for MetricRecord (which only accepts flat key-value pairs)
    # Convert nested dict to dotted keys: evaluation.model_name.metric_name
    flattened_metrics = {"num-examples": int(len(test_loader.dataset))}
    for model_name, model_results in evaluation_results.items():
        for metric_name, metric_value in model_results.items():
            flattened_key = f"evaluation.{model_name}.{metric_name}"
            # Ensure native Python types (convert from numpy if needed)
            flattened_metrics[flattened_key] = float(metric_value)

    log(INFO, f"DEBUG: Sending flattened_metrics: {flattened_metrics}")

    # Construct and return the reply Message
    # Send client_name in ConfigRecord (MetricRecord only accepts numeric types)
    metric_record = MetricRecord(flattened_metrics)
    config_record = ConfigRecord({"client_name": client_name})
    content = RecordDict({"metrics": metric_record, "config": config_record})
    return Message(content=content, reply_to=msg)
