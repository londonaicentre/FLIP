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
"""Client-API evaluation job for federated nuclei detection on IDC digital pathology.

Defines the job with :class:`~flip.nvflare.recipes.FlipEvalRecipe`, which wires NVFLARE's
``GlobalModelEval`` behind ``EvaluationModelLocator``: the server loads the checkpoint named in
``config.json['models']`` and broadcasts it to every client as one ``validate`` task, and each client
runs ``evaluator.py`` against its own slides and returns aggregate counts.

Two execution modes, exactly as the other FLIP evaluation tutorials:

*Export* (``--export --export-dir <path>``):
    Writes the complete NVFLARE job to ``<path>/flip_evaluation/``. Needs no GPU, no data and no
    checkpoint beyond the small specification file, so it is the cheap way to verify the wiring.

*SimEnv* (default):
    Runs the NVFLARE simulator over the local per-site data. Needs the tutorial dataset
    (``make -C fl-tutorials download-idc-pathology-data``) but no GPU -- the shipped detector is
    classical and runs on CPU.

Per-site data is selected inside the evaluator from ``flare.get_site_name()``, reading the
``SITE<N>_*`` variables this tutorial's Makefile exports from ``.env.app``.

Usage::

    python job.py --export --export-dir ./fl_job --n_clients 2
    python job.py --n_clients 2
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# app_files/ must be importable before the recipe is constructed: PTFileModelPersistor resolves
# "models.get_model" by string at construction time.
_APP_FILES_DIR = Path(__file__).parent / "app_files"
if str(_APP_FILES_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_FILES_DIR))

from flip.nvflare.recipes import FlipEvalRecipe  # noqa: E402
from nvflare import FedJob  # noqa: E402
from nvflare.recipe import SimEnv  # noqa: E402


def stage_app_files(job: FedJob) -> None:
    """Bundle every file in ``app_files/`` into the job's server and client ``custom/`` directories.

    The recipe wires only NVFLARE components; without this the evaluator and its modules never reach
    the sites. ``FedJob.add_file_to_*`` registers them with the job's file sources, so one call serves
    both the SimEnv run and ``--export``.

    Note the ``is_file()`` guard: **subdirectories are not staged**. ``app_files/`` is deliberately
    flat, because a nested package here would be silently dropped -- by this loop, and equally by the
    platform's own bundler, which walks an allowlist and fails closed.
    """
    for source in sorted(_APP_FILES_DIR.iterdir()):
        if source.is_file():
            job.add_file_to_server(str(source))
            job.add_file_to_clients(str(source))


def main() -> None:
    parser = argparse.ArgumentParser(description="FLIP IDC pathology nuclei-detection evaluation job")
    parser.add_argument("--n_clients", type=int, default=2, help="Number of clients")
    parser.add_argument(
        "--workspace", type=str, default="/tmp/nvflare/idc_pathology_nuclei_detection", help="SimEnv workspace root"
    )
    # --export / --export-dir are consumed by NVFLARE's Recipe.execute before this parser runs.
    args = parser.parse_args()

    # SQL contains spaces, which NVFLARE's whitespace-splitting task args cannot carry, so FLIP values
    # come from the environment and the recipe writes the query into config_fed_client.json.
    project_id = os.environ.get("FLIP_PROJECT_ID", "")
    query = os.environ.get("FLIP_QUERY", "SELECT * FROM Table;")

    recipe = FlipEvalRecipe(
        min_clients=args.n_clients,
        eval_script="evaluator.py",
        eval_args="--project_id {project_id}",
        project_id=project_id,
        query=query,
    )

    stage_app_files(recipe.job)

    run = recipe.execute(SimEnv(num_clients=args.n_clients, num_threads=args.n_clients, workspace_root=args.workspace))
    print()
    print(f"Job status:  {run.get_status()}")
    print(f"Job results: {run.get_result()}")
    print()


if __name__ == "__main__":
    main()
