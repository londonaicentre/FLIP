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

"""Client-API evaluation job definition for 3D spleen segmentation with FLIP.

This module defines the federated evaluation job using :class:`FlipEvalRecipe`. It supports two
execution modes:

*Export* (``--export --export-dir <path>``):
    Writes the complete NVFLARE job to ``<path>/flip_evaluation/``, including ``meta.json`` (carrying
    ``custom_props.model_id`` for lazy resolution), ``app/config/`` (server + client configs), and
    ``app/custom/`` (the bundled ``flip/`` package plus the staged user app files, including the
    ``model.pt`` checkpoint the server loads). This is the primary, fully-local-verifiable path — no
    GPU or data required.

*SimEnv* (default, no flags):
    Runs a local simulation under the NVFLARE simulator. Requires a GPU, the reference dataset
    (``make -C fl-tutorials download-spleen-data``), and the checkpoint (``make download-checkpoints``).
    FLIP-specific values are injected via environment variables (``FLIP_PROJECT_ID``, ``FLIP_QUERY``).

Both modes go through NVFLARE's :meth:`Recipe.execute`, which consumes ``--export``/``--export-dir`` from
``sys.argv`` itself (it strips them before this script's own ``argparse`` runs) and exports-or-runs
accordingly — so there is no separate ``--export`` branch here.

Usage:
    # Export job config for review or Docker deployment (no GPU needed)
    python job.py --export --export-dir ./fl_job --n_clients 2

    # SimEnv local simulation (requires GPU + data + checkpoint)
    python job.py --n_clients 2
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# app_files/ must be importable at recipe-construction time so PTFileModelPersistor can reference
# 'models.get_model' correctly.
_APP_FILES_DIR = Path(__file__).parent / "app_files"
if str(_APP_FILES_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_FILES_DIR))

from flip.nvflare.recipes import FlipEvalRecipe  # noqa: E402
from nvflare import FedJob  # noqa: E402
from nvflare.recipe import SimEnv  # noqa: E402


def stage_app_files(job: FedJob) -> None:
    """Bundle every file in ``app_files/`` into the job's server + client ``custom/`` directories.

    The recipe wires only the NVFLARE components; the user-supplied app files (``evaluator.py``,
    ``models.py``, ``transforms.py``, ``config.json`` and the ``model.pt`` checkpoint) must be added to
    the job explicitly so they land in every site's ``app/custom/``. ``FedJob.add_file_to_{server,clients}``
    registers them with the job's file sources, so they are bundled for **both** the SimEnv/simulator run
    (``recipe.execute``) and ``--export`` — unlike a post-export filesystem copy, which the SimEnv path
    never triggered.

    Args:
        job: The recipe's underlying :class:`~nvflare.job_config.api.FedJob` (``recipe.job``).
    """
    for src in sorted(_APP_FILES_DIR.iterdir()):
        if src.is_file():
            job.add_file_to_server(str(src))
            job.add_file_to_clients(str(src))


def main() -> None:
    parser = argparse.ArgumentParser(description="FLIP 3D Spleen Segmentation Evaluation Client API Job")
    parser.add_argument("--n_clients", type=int, default=2, help="Number of clients")
    parser.add_argument(
        "--workspace",
        type=str,
        default="/tmp/nvflare/spleen_eval",
        help="SimEnv workspace root",
    )
    # NOTE: ``--export``/``--export-dir`` are handled by NVFLARE's ``Recipe.execute`` (it strips them
    # from ``sys.argv`` before this parser runs), so they are intentionally not declared here.
    args = parser.parse_args()

    # FLIP-specific values are read from environment variables rather than CLI flags: SQL queries
    # contain spaces that don't survive argparse whitespace-splitting when forwarded via task args.
    # The evaluator reads the query from config_fed_client.json at runtime (via load_query()), and
    # project_id is passed as --project_id {project_id} which the FLIP-API substitutes before submission.
    project_id = os.environ.get("FLIP_PROJECT_ID", "")
    query = os.environ.get("FLIP_QUERY", "SELECT * FROM Table;")

    recipe = FlipEvalRecipe(
        min_clients=args.n_clients,
        eval_script="evaluator.py",
        eval_args="--project_id {project_id}",
        project_id=project_id,
        query=query,
    )

    # Stage the user app files into the job's custom/ dirs *before* execute() — this is what makes the
    # exported/simulated job self-contained (the recipe only wires components). execute() then either
    # exports (when --export is on sys.argv, exiting afterwards) or runs the SimEnv simulation.
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
