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
import traceback
from logging import ERROR, INFO
from pathlib import Path

import torch
from flip import FLIP
from flip.constants import PTConstants
from flip.constants.flip_constants import ModelStatus
from flip.flower.selection import parse_best_model_run_config
from flwr.app import ArrayRecord, Context
from flwr.common import log
from flwr.serverapp import Grid, ServerApp

from app.strategy import (
    FedAvgWithClientMetrics,
    per_client_eval_metrics,
    per_client_train_metrics,
)

FinalModelFilename = PTConstants.PTFileModelName
BestModelFilename = PTConstants.PTBestFileModelName
CrossValResultsJsonFilename = PTConstants.CrossValResultsJsonFilename


# Create ServerApp
app = ServerApp()


def _relay_failure(flip: FLIP, model_id: str) -> None:
    """Report the active exception to the hub, best-effort (FLIP#1006).

    The traceback goes through ``send_handled_exception`` (a ``success=false`` feed row —
    the ServerApp runs on the Central Hub, so the full traceback crosses no trust
    boundary) and the model is settled to ``ERROR``. Each step is guarded separately: a
    hub that cannot be reached, or a non-UUID model id (tutorial/simulator contexts),
    must not mask the original failure, which the caller re-raises.
    """
    try:
        flip.send_handled_exception(traceback.format_exc(), client_name=None, model_id=model_id)
    except Exception:
        log(ERROR, "Failed to relay the ServerApp exception to the hub:\n%s", traceback.format_exc())
    try:
        flip.update_status(model_id, ModelStatus.ERROR)
    except Exception:
        log(ERROR, "Failed to report ERROR status to the hub:\n%s", traceback.format_exc())


@app.main()
def main(grid: Grid, context: Context, flip: FLIP = FLIP()) -> None:
    """Main entry point for the ServerApp.

    A thin relay shell: any exception escaping the run is reported to the hub before it
    propagates, so a failed run shows its traceback on the model's activity feed instead
    of waiting for the hub's failed-job sweep to fetch a log tail (FLIP#1001/#1006). The
    re-raise keeps Flower's own ``finished:failed`` accounting intact.
    """
    model_id = context.run_config.get("flip-model-id", "monai-flower-tutorial-model")
    try:
        _run(grid, context, flip, model_id)
    except Exception:
        _relay_failure(flip, model_id)
        raise


def _run(grid: Grid, context: Context, flip: FLIP, model_id: str) -> None:
    """The training run itself — everything here is covered by ``main``'s relay."""
    run_config = context.run_config
    num_rounds = int(run_config.get("num-server-rounds", 1))

    flip.update_status(model_id, ModelStatus.INITIATED)

    # Imported here rather than at module scope: models.py is researcher-supplied, so an
    # import error in it must land in main's relay instead of killing the ServerApp
    # before it can report anything (FLIP#1006).
    from app.models import get_model

    # Best-model selection is opt-in: an unset or blank metric leaves behaviour unchanged
    # (final-round-only evaluation, no best checkpoint). Parsing lives in flip.flower — a
    # coerced metric key or a quoted TOML boolean would otherwise enable selection on a key
    # the clients never report, or silently invert the selection direction.
    try:
        best_model_metric, best_model_metric_minimize = parse_best_model_run_config(run_config, num_rounds=num_rounds)
    except ValueError as err:
        # Fail the run rather than mislabel a model. ERROR is the only channel the researcher
        # can actually see — the ServerApp log stream is not surfaced through the platform.
        log(INFO, f"✗ {err}")
        flip.update_status(model_id, ModelStatus.ERROR)
        raise

    model = get_model()
    flip.update_status(model_id, ModelStatus.PREPARED)

    arrays = ArrayRecord(model.state_dict())

    # Use FedAvg strategy with per-client metrics tracking
    strategy = FedAvgWithClientMetrics(
        flip=flip,
        model_id=model_id,
        fraction_train=1.0,
        fraction_evaluate=1.0,
        best_model_metric=best_model_metric,
        best_model_metric_minimize=best_model_metric_minimize,
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

    # Save the best model alongside it when a selection actually happened — nothing is
    # fabricated from the final model otherwise. Deliberately outside the try above: the
    # best checkpoint is optional and is a second full-size write (the one likely to hit
    # ENOSPC), so failing it must not discard an intact final model and its results.
    best_model_saved = False
    if strategy.best_model_arrays is not None:
        try:
            torch.save(strategy.best_model_arrays.to_torch_state_dict(), output_dir / BestModelFilename)
            best_model_saved = True
            log(
                INFO,
                "✓ Best model (round %s, %s=%s) saved to %s",
                strategy.best_model_round,
                best_model_metric,
                strategy.best_model_metric_value,
                output_dir / BestModelFilename,
            )
        except Exception as e:
            log(INFO, "⚠ Failed to save best model — the final model is unaffected: %s", str(e))

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

    # Add per-client metrics from the last evaluated round (the final round —
    # best-model selection also evaluates earlier rounds, but the cross-site
    # table reports the final model)
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
    # Keyed on the write succeeding, not on a selection happening, so the JSON never names
    # a checkpoint that is missing from the results bundle.
    if best_model_saved:
        cross_val_results["best_model"] = BestModelFilename
        cross_val_results["best_round"] = strategy.best_model_round
        cross_val_results["best_metric"] = {
            "name": best_model_metric,
            "value": strategy.best_model_metric_value,
            "minimize": best_model_metric_minimize,
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
        flip.update_status(model_id, ModelStatus.ERROR)
        return

    log(INFO, "\n✓ Training complete. All outputs saved to %s", output_dir)
    log(INFO, "  - Model: %s", FinalModelFilename)
    log(INFO, "  - Results JSON: %s", CrossValResultsJsonFilename)
