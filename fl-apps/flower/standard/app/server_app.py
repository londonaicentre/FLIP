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
import sys
from logging import INFO
from pathlib import Path
from typing import Iterable

import torch
from app.models import get_model
from flip import FLIP
from flip.constants.flip_constants import ModelStatus
from flwr.app import ArrayRecord, Context, Message, MetricRecord
from flwr.common import ConfigRecord, log
from flwr.serverapp import Grid, ServerApp
from flwr.serverapp.strategy import FedAvg

try:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from run import CrossValResultsJsonFilename, FinalModelFilename
except ImportError:
    FinalModelFilename = "FL_global_model.pt"
    CrossValResultsJsonFilename = "cross_val_results.json"

# Dictionary to store per-client metrics
per_client_train_metrics: dict[int, dict[str, dict]] = {}
per_client_eval_metrics: dict[int, dict[str, dict]] = {}


class CustomFedAvg(FedAvg):
    """Custom FedAvg strategy that captures per-client train and evaluation metrics."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.num_rounds = None

    def start(self, grid: Grid, initial_arrays: ArrayRecord, num_rounds: int = 3, **kwargs):
        """Override start to capture num_rounds for evaluation control."""
        self.num_rounds = num_rounds
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
        # Store per-client training metrics before aggregation
        for msg in replies:
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
        # Store per-client metrics before aggregation
        for msg in replies:
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

    # Use custom FedAvg strategy to capture per-client metrics
    strategy = CustomFedAvg(
        fraction_train=1.0,
        fraction_evaluate=1.0,
    )

    result = strategy.start(
        grid=grid,
        initial_arrays=arrays,
        num_rounds=num_rounds,
    )

    flip.update_status(model_id, ModelStatus.TRAINING_STARTED)

    log(INFO, f"\n{'=' * 60}")
    log(INFO, "Training and evaluation complete!")
    log(INFO, f"{'=' * 60}")

    # Get output directory from constants
    output_dir = Path(f"/app/{model_id}/trainig_outputs")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save final model to disk using constant filename
    log(INFO, "Saving %s to %s...", FinalModelFilename, output_dir)
    state_dict = result.arrays.to_torch_state_dict()
    torch.save(state_dict, output_dir / FinalModelFilename)
    log(INFO, "✓ Final model saved to %s", output_dir / FinalModelFilename)

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

    flip.upload_results_to_s3(output_dir, model_id)
    flip.update_status(model_id, ModelStatus.RESULTS_UPLOADED)

    log(INFO, "\n✓ Training complete. All outputs saved to %s", output_dir)
    log(INFO, "  - Model: %s", FinalModelFilename)
    log(INFO, "  - Results JSON: %s", CrossValResultsJsonFilename)
