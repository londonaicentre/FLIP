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
import shutil
import traceback
from logging import ERROR, INFO
from pathlib import Path

import torch
from flip import FLIP
from flip.constants.flip_constants import ModelStatus
from flwr.app import ArrayRecord, Context
from flwr.common import log
from flwr.serverapp import Grid, ServerApp

from app.strategy import EvaluationStrategy

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
    model_id = context.run_config.get("flip-model-id", "monai-flower-evaluation-model")
    try:
        _run(grid, context, flip, model_id)
    except Exception:
        _relay_failure(flip, model_id)
        raise


def _run(grid: Grid, context: Context, flip: FLIP, model_id: str) -> None:
    """The evaluation run itself — everything here is covered by ``main``'s relay."""
    run_config = context.run_config
    num_rounds = int(run_config.get("num-server-rounds", 1))

    flip.update_status(model_id, ModelStatus.INITIATED)

    # ------------------------------------------------------------------
    # Locate and load the model checkpoint.
    #
    # ``flip-job-dir`` is injected as a run-config override by the FL API at
    # submission time and points at the app directory on the shared volume,
    # where the uploaded .pt checkpoint lives next to config.toml and the app
    # sources. ``checkpoint`` is the checkpoint filename within that directory.
    # ------------------------------------------------------------------
    app_dir = run_config.get("flip-job-dir")
    if not app_dir:
        msg = "flip-job-dir is not set in the run config"
        log(ERROR, msg)
        raise ValueError(msg)

    checkpoint_name = run_config.get("checkpoint")
    if not checkpoint_name:
        msg = "checkpoint is not set in the run config"
        log(ERROR, msg)
        raise ValueError(msg)

    checkpoint_file = os.path.join(app_dir, checkpoint_name)
    if not os.path.exists(checkpoint_file):
        msg = f"Checkpoint not found at: {checkpoint_file}"
        log(ERROR, msg)
        raise FileNotFoundError(msg)

    # Imported here rather than at module scope: models.py is researcher-supplied, so an
    # import error in it must land in main's relay instead of killing the ServerApp
    # before it can report anything (FLIP#1006).
    from app.models import get_model

    model = get_model()
    # Load to CPU: the SuperLink runs CPU-only and only repacks the weights for distribution to SuperNodes.
    state_dict = torch.load(checkpoint_file, map_location="cpu", weights_only=True)
    # strict=True (default) — a missing/unexpected key means the on-disk checkpoint
    # doesn't match get_model()'s architecture, and the eval would otherwise run
    # with the mismatched layers still at random init and report a plausible-but-
    # meaningless dice. Fail loudly here instead.
    model.load_state_dict(state_dict)
    log(INFO, f"Loaded model from {checkpoint_file}")

    flip.update_status(model_id, ModelStatus.PREPARED)

    # Pack the model weights into an ArrayRecord for distribution to clients.
    arrays = ArrayRecord(model.state_dict())

    # Federated evaluation strategy. FedAvg aggregates whatever metrics the
    # clients return in their MetricRecord (weighted by num-examples); the hub
    # forwarding lives in FlipFedAvg, and EvaluationStrategy adds only the
    # per-client metric breakdown.
    strategy = EvaluationStrategy(
        flip=flip,
        model_id=model_id,
        fraction_train=0.0,  # No training
        fraction_evaluate=1.0,  # All clients evaluate
    )

    # The evaluation rounds start here — mirrors the standard template's strategy,
    # which reports RUNNING when its rounds start (#782).
    flip.update_status(model_id, ModelStatus.RUNNING)

    _ = strategy.start(
        grid=grid,
        initial_arrays=arrays,
        num_rounds=num_rounds,
    )

    log(INFO, f"\n{'=' * 60}")
    log(INFO, "Evaluation complete!")
    log(INFO, f"{'=' * 60}")

    # Get output directory using WORKING_DIR environment variable
    working_dir = os.getenv("WORKING_DIR", "/app/runs")
    output_dir = Path(f"{working_dir}/{model_id}/evaluation_outputs")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Prepare evaluation results
    evaluation_results = {
        "num_rounds": num_rounds,
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

    # Clean up the job folder once evaluation is done. flip-job-dir points at the
    # app directory on the shared volume; its parent is the job folder (the
    # uploaded bundle: sources + checkpoint). Removing it frees the upload.
    job_dir = Path(app_dir).parent
    if job_dir.exists():
        # Only delete if the path contains the model_id (safety check)
        if model_id in str(job_dir):
            try:
                shutil.rmtree(job_dir)
                log(INFO, "✓ Cleaned up job folder: %s", job_dir)
            except Exception as e:
                log(INFO, "Warning: Failed to clean up job folder %s: %s", job_dir, str(e))
        else:
            log(INFO, "Skipping cleanup: job folder %s does not contain model_id", job_dir)
