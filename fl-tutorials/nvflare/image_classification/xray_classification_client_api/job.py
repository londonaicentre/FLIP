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

"""FedAvg Client API job definition for X-Ray Classification with FLIP.

This module defines the federated learning job using :class:`FlipFedAvgRecipe`.
It supports two execution modes:

*Export* (``--export --export-dir <path>``):
    Writes the complete NVFLARE job to ``<path>/flip_fedavg/``, including
    ``meta.json`` (carrying ``custom_props.model_id`` for lazy resolution),
    ``app/config/`` (server + client configs), and ``app/custom/`` (the bundled
    ``flip/`` package plus the staged user app files). This is the primary,
    fully-local-verifiable path — no GPU or data required.

*SimEnv* (default, no flags):
    Runs a local simulation under the NVFLARE simulator. Requires a GPU and the
    reference dataset (``make -C fl-tutorials download-xray-data``). FLIP-specific
    values are injected via environment variables (``FLIP_PROJECT_ID``,
    ``FLIP_QUERY``) rather than CLI flags because SQL queries contain spaces that
    don't survive argparse whitespace-splitting.

Usage:
    # Export job config for review or Docker deployment (no GPU needed)
    python job.py --export --export-dir ./fl_job --n_clients 2 --num_rounds 3

    # SimEnv local simulation (requires GPU + data)
    python job.py --n_clients 2 --num_rounds 3
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

# app_files/ must be importable at recipe-construction time so PTFileModelPersistor
# and PTModelLocator can reference 'models.get_model' correctly.
_APP_FILES_DIR = Path(__file__).parent / "app_files"
if str(_APP_FILES_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_FILES_DIR))

from flip.nvflare.recipes import FlipFedAvgRecipe  # noqa: E402
from nvflare.recipe import SimEnv  # noqa: E402


def stage_app_files(job_root: str) -> None:
    """Copy every file in app_files/ into <job_root>/flip_fedavg/app/custom/.

    The recipe's :meth:`export` auto-bundles the ``flip/`` package into
    ``app/custom/flip/`` but does not include the user-supplied app files
    (trainer.py, config.json, models.py, data_utils.py, loss_and_metrics.py).
    This function completes the job by staging those files alongside the
    bundled ``flip/`` package.

    Args:
        job_root: The export-dir passed to :meth:`export` (e.g. ``./fl_job``).
    """
    custom_dir = Path(job_root) / "flip_fedavg" / "app" / "custom"
    app_files_dir = Path(__file__).parent / "app_files"
    for src in app_files_dir.iterdir():
        if src.is_file():
            shutil.copy2(src, custom_dir / src.name)


def main() -> None:
    parser = argparse.ArgumentParser(description="FLIP X-Ray Classification Client API FedAvg Job")
    parser.add_argument("--n_clients", type=int, default=2, help="Number of clients")
    parser.add_argument("--num_rounds", type=int, default=3, help="Number of federated rounds")
    parser.add_argument(
        "--workspace",
        type=str,
        default="/tmp/nvflare/xray_client_api",
        help="SimEnv workspace root",
    )
    parser.add_argument("--export", action="store_true", help="Export job config instead of running SimEnv")
    parser.add_argument(
        "--export-dir",
        type=str,
        default="./fl_job",
        help="Directory to write the exported job (default: ./fl_job)",
    )
    args = parser.parse_args()

    # ------------------------------------------------------------------
    # FLIP-specific values are read from environment variables rather than
    # CLI flags.  SQL queries contain spaces that don't survive argparse
    # whitespace-splitting when forwarded via train_args.  The trainer
    # reads the query from config_fed_client.json at runtime (via
    # load_query()), and project_id is passed as --project_id {project_id}
    # which the FLIP-API substitutes before job submission.
    # ------------------------------------------------------------------
    project_id = os.environ.get("FLIP_PROJECT_ID", "")
    query = os.environ.get("FLIP_QUERY", "SELECT * FROM Table;")

    recipe = FlipFedAvgRecipe(
        num_rounds=args.num_rounds,
        min_clients=args.n_clients,
        train_script="trainer.py",
        train_args="--project_id {project_id}",
        project_id=project_id,
        query=query,
    )

    if args.export:
        recipe.export(args.export_dir)
        stage_app_files(args.export_dir)
        print(f"Exported complete job to: {Path(args.export_dir).resolve() / 'flip_fedavg'}")
    else:
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
