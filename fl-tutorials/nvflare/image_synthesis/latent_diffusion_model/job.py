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

"""Latent diffusion FedAvg Client API job definition for FLIP.

Trains a diffusion model inside a **frozen, uploaded** autoencoder's latent space as an ordinary
single-stage FedAvg job, using :class:`FlipFedAvgRecipe`. It supports two execution modes:

*Export* (``--export --export-dir <path>``):
    Writes the complete NVFLARE job to ``<path>/flip_fedavg/``, including ``meta.json`` (carrying
    ``custom_props.model_id`` for lazy resolution), ``app/config/`` (server + client configs), and
    ``app/custom/`` (the bundled ``flip/`` package plus the staged user app files). This is the
    primary, fully-local-verifiable path — no GPU or data required.

*SimEnv* (default, no flags):
    Runs a local simulation under the NVFLARE simulator. Requires a GPU, the reference dataset
    (``make -C fl-tutorials download-spleen-data``) and the prepared autoencoder checkpoint
    (``make prepare-checkpoint``). FLIP-specific values are injected via environment variables
    (``FLIP_PROJECT_ID``, ``FLIP_QUERY``) rather than CLI flags because SQL queries contain spaces
    that don't survive argparse whitespace-splitting.

Frozen-autoencoder specifics, driven by ``app_files/config.json``:

* ``SERVER_CHECKPOINT`` — the autoencoder-only checkpoint is seeded into the round-0 global model
  server-side by ``InitialCheckpointPTModelPersistor`` (wired into ``FlipFedAvgRecipe``). It is
  staged to the **server** custom dir only (see :func:`stage_app_files`), so clients build a bare
  architecture and receive the autoencoder at round 0 — they never need the file.
* ``AGGREGATE_ONLY_REGEX`` — passed to the recipe so it wires the diffusion-model-only filters
  (``KeepOnlyVars`` client-side return, ``TrimBroadcastVars`` server-side broadcast after round 0,
  ``ReconstructFullModel`` client-side rebuild). This reproduces locally what the fl-server injects
  from the same config key at deploy, so ``make run``/``make sim`` exercises the full frozen-encoder
  round-trip. It is **not merely an optimisation**: leaving the frozen autoencoder's zero diffs in
  the update flattens ``PercentilePrivacy``'s (global) percentile cutoff to zero, silently turning
  the DP sparsification into a no-op.

Both modes go through NVFLARE's :meth:`Recipe.execute`, which consumes ``--export``/``--export-dir``
from ``sys.argv`` itself (it strips them before this script's own ``argparse`` runs) and
exports-or-runs accordingly — so there is no separate ``--export`` branch here.

Usage:
    # Export job config for review or Docker deployment (no GPU needed)
    python job.py --export --export-dir ./fl_job --n_clients 2 --num_rounds 1

    # SimEnv local simulation (requires GPU + data + checkpoint)
    python job.py --n_clients 2 --num_rounds 1
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# app_files/ must be importable at recipe-construction time so PTFileModelPersistor / PTModelLocator
# can reference 'models.get_model' correctly.
_APP_FILES_DIR = Path(__file__).parent / "app_files"
if str(_APP_FILES_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_FILES_DIR))

from flip.nvflare.recipes import FlipFedAvgRecipe  # noqa: E402
from nvflare import FedJob  # noqa: E402
from nvflare.recipe import SimEnv  # noqa: E402

# The autoencoder checkpoint filename, matching config.json's SERVER_CHECKPOINT. Staged server-side
# only. Kept as a constant here (rather than read from the config) so the two are checked against
# each other by fl-tutorials/tests/test_image_synthesis_config_parity.py.
_CHECKPOINT_NAME = "pretrained_autoencoder.pt"


def stage_app_files(job: FedJob) -> None:
    """Bundle every file in ``app_files/`` into the job's ``custom/`` directories.

    Everything is added to both the server and the clients EXCEPT the autoencoder checkpoint
    (``pretrained_autoencoder.pt``), which is added to the **server** only.
    ``InitialCheckpointPTModelPersistor`` loads it into the round-0 global model and
    ``ScatterAndGather`` broadcasts that model to every client, so clients build only a bare
    architecture and receive the frozen autoencoder at round 0 — they never need the file.
    ``FedJob.add_file_to_{server,clients}`` registers the files with the job's file sources, so they
    are bundled for both the SimEnv run (``recipe.execute``) and ``--export``.

    Note where the server-only guarantee actually lives. This export produces a **single shared**
    ``app/`` directory (``deploy_map: {"app": ["@ALL"]}``), so locally the distinction has no
    observable effect — the checkpoint sits in that one ``custom/`` dir, which is exactly what the
    persistor's ``<app>/custom/<checkpoint>`` resolution step wants for a simulator run. In
    production the checkpoint never enters the job bundle at all: flip-api's bundler diverts any
    file named by ``SERVER_CHECKPOINT`` to ``<bucket>/server_checkpoints/``, and the FL API stages it
    to ``<SERVER_CHECKPOINT_ROOT>/<model_id>/`` on a hub-local volume that no client can read. The
    exclusion here expresses the same intent for the local path and matches ``arkplus_fine_tuning``,
    which behaves identically.

    Args:
        job: The recipe's underlying :class:`~nvflare.job_config.api.FedJob` (``recipe.job``).
    """
    for src in sorted(_APP_FILES_DIR.iterdir()):
        if not src.is_file():
            continue
        job.add_file_to_server(str(src))
        if src.name != _CHECKPOINT_NAME:
            job.add_file_to_clients(str(src))


def _load_aggregate_only_regex() -> str:
    """Read ``AGGREGATE_ONLY_REGEX`` from the app config so the recipe can wire the head-only filters."""
    cfg = json.loads((_APP_FILES_DIR / "config.json").read_text())
    return cfg.get("AGGREGATE_ONLY_REGEX", "") or ""


def main() -> None:
    parser = argparse.ArgumentParser(description="FLIP Latent Diffusion Client API FedAvg Job")
    parser.add_argument("--n_clients", type=int, default=2, help="Number of clients")
    parser.add_argument("--num_rounds", type=int, default=1, help="Number of federated rounds")
    parser.add_argument(
        "--workspace",
        type=str,
        default="/tmp/nvflare/ldm",
        help="SimEnv workspace root",
    )
    # NOTE: ``--export``/``--export-dir`` are handled by NVFLARE's ``Recipe.execute`` (it strips them
    # from ``sys.argv`` before this parser runs), so they are intentionally not declared here.
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
        aggregate_only_regex=_load_aggregate_only_regex(),
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
