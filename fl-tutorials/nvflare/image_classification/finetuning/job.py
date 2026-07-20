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

"""FedAvg Client API job definition for the Ark+ fine-tuning experiment.

Local-simulator replica of the UK--Thailand production run: same job type
(``standard_client_api``), same FLIP ScatterAndGather controller and
PercentilePrivacy filter (via :class:`FlipFedAvgRecipe`), same app files.
Because the controller is identical, the simulator log carries the same
``Round N started./finished.``, ``Start/End aggregation.``, and
``Contribution ... ACCEPTED`` lines that ``scripts/extract_model_metrics.sh``
parses from CloudWatch in production — extract them locally with
``scripts/extract_simulator_metrics.sh`` for a platform-overhead baseline.

Defaults mirror the paper's run: 2 clients, 50 rounds (local epochs, batch
size, LR schedule etc. come from ``app_files/config.json`` as in production).

Usage:
    # Export job config for review (no GPU needed)
    python job.py --export --export-dir ./fl_job --n_clients 2 --num_rounds 50

    # SimEnv local simulation (requires GPU + data; see .env.app)
    python job.py --n_clients 2 --num_rounds 50
"""

from __future__ import annotations

import argparse
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

    The recipe wires only the NVFLARE components; the user-supplied app files (``trainer.py``,
    ``models.py``, ``config.json``, the Ark+ modules, and ``pretrained_weights.pt``) must be added
    to the job explicitly so they land in every site's ``app/custom/`` — exactly the layout the
    production upload produces.

    Args:
        job: The recipe's underlying :class:`~nvflare.job_config.api.FedJob` (``recipe.job``).
    """
    for src in sorted(_APP_FILES_DIR.iterdir()):
        if src.is_file():
            job.add_file_to_server(str(src))
            job.add_file_to_clients(str(src))


def main() -> None:
    parser = argparse.ArgumentParser(description="FLIP Ark+ fine-tuning Client API FedAvg job (simulator replica)")
    parser.add_argument("--n_clients", type=int, default=2, help="Number of clients")
    parser.add_argument("--num_rounds", type=int, default=50, help="Number of federated rounds")
    parser.add_argument(
        "--workspace",
        type=str,
        default="/tmp/nvflare/finetuning",
        help="SimEnv workspace root (extract_simulator_metrics.sh reads logs from here)",
    )
    # NOTE: ``--export``/``--export-dir`` are handled by NVFLARE's ``Recipe.execute`` (it strips them
    # from ``sys.argv`` before this parser runs), so they are intentionally not declared here.
    args = parser.parse_args()

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
