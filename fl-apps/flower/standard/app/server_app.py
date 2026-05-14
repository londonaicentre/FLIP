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

"""quickstart-monai: A Flower / MONAI server app (training-only)."""

import json
import os
from logging import INFO
from pathlib import Path

import torch
from flip import FLIP
from flip.constants import PTConstants
from flip.constants.flip_constants import ModelStatus
from flwr.app import ArrayRecord, Context
from flwr.common import log
from flwr.serverapp import Grid, ServerApp

from app.models import get_model
from app.strategy import (
    FedAvgWithClientMetrics,
    per_client_eval_metrics,
    per_client_train_metrics,
)

FinalModelFilename = PTConstants.PTFileModelName
CrossValResultsJsonFilename = PTConstants.CrossValResultsJsonFilename


# Create ServerApp
app = ServerApp()


@app.main()
def main(grid: Grid, context: Context, flip: FLIP = FLIP()) -> None:
    """Main entry point for the ServerApp."""

    run_config = context.run_config
    model_id = run_config.get("flip-model-id", "monai-flower-tutorial-model")
    num_rounds = int(run_config.get("num-server-rounds", 1))

    flip.update_status(model_id, ModelStatus.INITIATED)

    model = get_model()
    flip.update_status(model_id, ModelStatus.PREPARED)

    arrays = ArrayRecord(model.state_dict())

    # Use FedAvg strategy with per-client metrics tracking
    strategy = FedAvgWithClientMetrics(
        flip=flip,
        model_id=model_id,
        fraction_train=1.0,
        fraction_evaluate=1.0,
    )

    result = strategy.start(
        grid=grid,
        initial_arrays=arrays,
        num_rounds=num_rounds,
    )

    log(INFO, f"\n{'=' * 60}")
    log(INFO, "Training and evaluation complete!")
    log(INFO, f"{'=' * 60}")

    # Get output directory from constants
    working_dir = os.getenv("WORKING_DIR", "/app/runs")
    output_dir = Path(f"{working_dir}/{model_id}/training_outputs")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save final model to disk using constant filename
    log(INFO, "Saving %s to %s...", FinalModelFilename, output_dir)
    try:
        state_dict = result.arrays.to_torch_state_dict()
        torch.save(state_dict, output_dir / FinalModelFilename)
        log(INFO, "✓ Final model saved to %s", output_dir / FinalModelFilename)
    except Exception as e:
        log(INFO, "Failed to save final model: %s", str(e))
        flip.update_status(model_id, ModelStatus.ERROR)
        return

    # Save cross-validation results JSON with aggregated and per-client metrics
    eval_metrics_aggregated = {}
    if result.evaluate_metrics_clientapp:
        # Get the last round's aggregated evaluation metrics
        last_agg_round = max(result.evaluate_metrics_clientapp.keys())
        eval_metrics_aggregated = dict(result.evaluate_metrics_clientapp[last_agg_round])

    # Structure training metrics per round with aggregated + per-client
    train_metrics = {}
    for round_num, metrics in result.train_metrics_clientapp.items():
        train_metrics[str(round_num)] = {"aggregated": dict(metrics)}
        # Add per-client training metrics for this round
        if round_num in per_client_train_metrics:
            for site_name, site_metrics in per_client_train_metrics[round_num].items():
                train_metrics[str(round_num)][site_name] = site_metrics

    # Structure evaluation metrics: aggregated + per-client at same level
    evaluation_metrics = {"aggregated": eval_metrics_aggregated}

    # Add per-client metrics from the last round (since evaluation only happens once)
    if per_client_eval_metrics:
        last_eval_round = max(per_client_eval_metrics.keys())
        for site_name, metrics in per_client_eval_metrics[last_eval_round].items():
            evaluation_metrics[site_name] = metrics

    cross_val_results = {
        "num_rounds": num_rounds,
        "final_model": FinalModelFilename,
        "train_metrics": train_metrics,
        "evaluation_metrics": evaluation_metrics,
    }

    json_path = output_dir / CrossValResultsJsonFilename
    with open(json_path, "w") as f:
        json.dump(cross_val_results, f, indent=2)
    log(INFO, "✓ Cross-validation results saved to %s", json_path)

    try:
        flip.upload_results_to_s3(output_dir, model_id)
        flip.update_status(model_id, ModelStatus.RESULTS_UPLOADED)
    except Exception as e:
        log(INFO, "Failed to upload results to S3: %s", str(e))
        flip.update_status(model_id, ModelStatus.RESULTS_UPLOAD_FAILED)
        return

    log(INFO, "\n✓ Training complete. All outputs saved to %s", output_dir)
    log(INFO, "  - Model: %s", FinalModelFilename)
    log(INFO, "  - Results JSON: %s", CrossValResultsJsonFilename)
