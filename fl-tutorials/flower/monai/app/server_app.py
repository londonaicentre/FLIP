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

from flip import FLIP
from flip.constants.flip_constants import ModelStatus
from flwr.app import ArrayRecord, Context
from flwr.serverapp import Grid, ServerApp
from flwr.serverapp.strategy import FedAvg

from app.models import get_model

# Create ServerApp
app = ServerApp()


@app.main()
def main(grid: Grid, context: Context, flip: FLIP = FLIP()) -> None:
    """Main entry point for the ServerApp."""

    run_config = context.run_config
    num_rounds = int(run_config.get("num-server-rounds", 1))
    model_id = run_config.get("flip-model-id", "monai-flower-tutorial-model")

    flip.update_status(model_id, ModelStatus.INITIATED)

    model = get_model()
    flip.update_status(model_id, ModelStatus.PREPARED)

    arrays = ArrayRecord(model.state_dict())

    strategy = FedAvg(
        fraction_train=1.0,
        fraction_evaluate=0.0,
    )

    strategy.start(
        grid=grid,
        initial_arrays=arrays,
        num_rounds=num_rounds,
    )

    flip.update_status(model_id, ModelStatus.TRAINING_STARTED)
    flip.update_status(model_id, ModelStatus.RESULTS_UPLOADED)
