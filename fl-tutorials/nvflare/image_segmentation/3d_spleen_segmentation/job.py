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

"""FedAvg job definition for 3D Spleen Segmentation with FLIP.

This module defines the federated learning job using the FedAvgRecipe and
FLUNet model.  It supports two execution modes via ``recipe.execute()``:

*SimEnv* (default, no flags):
    Runs a local simulation with ``n_clients`` parallel threads.  Intended
    for development and integration testing.

*Export* (``--export --export-dir <path>``):
    Writes the job config to ``<path>`` as a set of NVFlare configuration
    files.  The exported job can be submitted to a production FL server
    (e.g. via Docker compose).

Usage:
    # SimEnv (local testing)
    python job.py --n_clients 2 --num_rounds 10 --local_epochs 4

    # Export (Docker / production)
    python job.py --export --export-dir ./fl_job --n_clients 2 --num_rounds 10
"""

from __future__ import annotations

import argparse
import os

from model import FLUNet
from nvflare.apis.dxo import DataKind
from nvflare.app_opt.pt.recipes.fedavg import FedAvgRecipe
from nvflare.client.config import TransferType
from nvflare.recipe import SimEnv


def main() -> None:
    parser = argparse.ArgumentParser(description="FLIP Spleen Segmentation FedAvg Job")
    parser.add_argument("--n_clients", type=int, default=2, help="Number of clients")
    parser.add_argument("--num_rounds", type=int, default=10, help="Number of federated rounds")
    parser.add_argument("--local_epochs", type=int, default=4, help="Local training epochs per round")
    parser.add_argument("--lr", type=float, default=5e-5, help="Learning rate")
    parser.add_argument("--val_split", type=float, default=0.1, help="Validation split fraction")
    parser.add_argument("--batch_size", type=int, default=3, help="Training batch size")
    parser.add_argument("--threads", type=int, default=2, help="SimEnv parallel threads")
    parser.add_argument(
        "--workspace",
        type=str,
        default="/tmp/nvflare/simulation",
        help="SimEnv workspace root",
    )
    args = parser.parse_args()

    # ------------------------------------------------------------------
    # Build train_args string — these are forwarded to client.py
    #
    # FLIP-specific values (project_id, query, model_id) are NOT passed
    # here because the SQL query contains spaces and special characters
    # that don't survive shell/argparse splitting.  Instead they are
    # injected via environment variables (FLIP_PROJECT_ID, FLIP_QUERY,
    # FLIP_MODEL_ID) which client.py reads as CLI defaults.
    # ------------------------------------------------------------------
    train_args = (
        f"--local_epochs {args.local_epochs} "
        f"--lr {args.lr} "
        f"--val_split {args.val_split} "
        f"--batch_size {args.batch_size}"
    )

    job_dir = os.path.dirname(os.path.abspath(__file__))

    # ------------------------------------------------------------------
    # Create FedAvgRecipe with FLUNet model
    # ------------------------------------------------------------------
    recipe = FedAvgRecipe(
        name="spleen_fedavg_flip",
        min_clients=args.n_clients,
        num_rounds=args.num_rounds,
        model=FLUNet(
            spatial_dims=3,
            in_channels=1,
            out_channels=2,
            channels=[16, 32, 64, 128, 256],
            strides=[2, 2, 2, 2],
            num_res_units=2,
            norm="batch",
        ),
        train_script=os.path.join(job_dir, "client.py"),
        train_args=train_args,
        # Weight diff reduces bandwidth
        params_transfer_type=TransferType.DIFF,
        aggregator_data_kind=DataKind.WEIGHT_DIFF,
        # The key metric for model selection
        key_metric="accuracy",
    )

    # ------------------------------------------------------------------
    # Execute (SimEnv) or Export
    # ------------------------------------------------------------------
    env = SimEnv(
        num_clients=args.n_clients,
        num_threads=args.threads,
        workspace_root=args.workspace,
    )
    run = recipe.execute(env)

    print()
    print(f"Job status:  {run.get_status()}")
    print(f"Job results: {run.get_result()}")
    print()


if __name__ == "__main__":
    main()
