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

"""FedAvg Client API job definition for federated causal discovery with FLIP.

The job is the platform's ordinary ``standard`` (FedAvg) job — the federation of FCI is done
entirely in what the sites send (see ``app_files/federated_fci.py``), so this module is the EHR
tutorial's ``job.py`` with the names changed. It supports two execution modes:

*Export* (``--export --export-dir <path>``):
    Writes the complete NVFLARE job to ``<path>/flip_fedavg/`` — ``meta.json``,
    ``app/config/`` and ``app/custom/`` (the bundled ``flip/`` package plus the staged
    ``app_files/``). Fully local, no data or GPU needed.

*SimEnv* (default, no flags):
    Runs the NVFLARE simulator on the CPU. Build the cohort from your own ADNI download first
    (``make -C fl-tutorials convert-adni-cohort ADNI_DIR=<IDA tables>``; the README's "Running on
    real ADNI data"). FLIP-specific values are injected via environment variables
    (``FLIP_PROJECT_ID``, ``FLIP_QUERY``).

Both modes go through NVFLARE's :meth:`Recipe.execute`, which consumes ``--export``/``--export-dir``
from ``sys.argv`` itself.

Usage:
    python job.py --export --export-dir ./fl_job --n_clients 2 --num_rounds 2
    python job.py --n_clients 2 --num_rounds 2
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# app_files/ must be importable at recipe-construction time so PTFileModelPersistor
# and PTModelLocator can reference 'models.get_model' correctly.
_APP_FILES_DIR = Path(__file__).parent / "app_files"
if str(_APP_FILES_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_FILES_DIR))

from flip.nvflare.recipes import FlipFedAvgRecipe  # noqa: E402
from nvflare import FedJob  # noqa: E402
from nvflare.recipe import SimEnv  # noqa: E402


def stage_app_files(job: FedJob) -> None:
    """Bundle every file in ``app_files/`` into the job's server + client ``custom/`` directories.

    Registered with the job's file sources so they are bundled for both the SimEnv run and
    ``--export`` (a post-export filesystem copy would miss the SimEnv path).

    Args:
        job: The recipe's underlying :class:`~nvflare.job_config.api.FedJob` (``recipe.job``).
    """
    for src in sorted(_APP_FILES_DIR.iterdir()):
        if src.is_file():
            job.add_file_to_server(str(src))
            job.add_file_to_clients(str(src))


def main() -> None:
    parser = argparse.ArgumentParser(description="FLIP biomarker causal discovery Client API FedAvg Job")
    parser.add_argument("--n_clients", type=int, default=2, help="Number of clients")
    parser.add_argument("--num_rounds", type=int, default=2, help="Number of federated rounds")
    parser.add_argument(
        "--workspace",
        type=str,
        default="/tmp/nvflare/causal",
        help="SimEnv workspace root",
    )
    # NOTE: ``--export``/``--export-dir`` are handled by NVFLARE's ``Recipe.execute`` (it strips them
    # from ``sys.argv`` before this parser runs), so they are intentionally not declared here.
    args = parser.parse_args()

    # FLIP-specific values come from environment variables: SQL queries contain spaces that do
    # not survive argparse whitespace-splitting via train_args. The trainer reads the query from
    # config_fed_client.json at run time (load_query()); project_id arrives as
    # --project_id {project_id}, substituted by the FLIP-API before submission.
    project_id = os.environ.get("FLIP_PROJECT_ID", "")
    query = os.environ.get("FLIP_QUERY", "SELECT * FROM Table;")

    # No best-model selection: the "model" is the federated state and "best" has no meaning for
    # it. An absent BEST_MODEL_METRIC wires no selector.
    config = json.loads((_APP_FILES_DIR / "config.json").read_text())

    recipe = FlipFedAvgRecipe(
        num_rounds=args.num_rounds,
        min_clients=args.n_clients,
        train_script="trainer.py",
        train_args="--project_id {project_id}",
        project_id=project_id,
        query=query,
        best_model_metric=config.get("BEST_MODEL_METRIC"),
        best_model_metric_minimize=config.get("BEST_MODEL_METRIC_MINIMIZE", False),
    )

    # Stage the user app files into the job's custom/ dirs *before* execute().
    stage_app_files(recipe.job)

    env = SimEnv(
        num_clients=args.n_clients,
        num_threads=args.n_clients,
        workspace_root=args.workspace,
    )
    run = recipe.execute(env)

    print()
    print(f"Job status:  {run.get_status()}")
    print(f"Job results: {run.get_result()}")
    print()


if __name__ == "__main__":
    main()
