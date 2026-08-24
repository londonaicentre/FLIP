# Copyright (c) 2026 Flower Labs GmbH
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

"""quickstart-numpy: A Flower / NumPy app."""

from flwr.app import ArrayRecord, Context
from flwr.serverapp import Grid, ServerApp
from flwr.serverapp.strategy import FedAvg

from app.task import get_dummy_model

# Create ServerApp
app = ServerApp()


@app.main()
def main(grid: Grid, context: Context) -> None:
    """Main entry point for the ServerApp."""

    # Read run config
    num_rounds: int = context.run_config["num-server-rounds"]

    # Load global model
    model = get_dummy_model()
    arrays = ArrayRecord(model)

    # Initialize FedAvg strategy
    strategy = FedAvg()

    # Start strategy, run FedAvg for `num_rounds`. The returned result carries the final arrays and
    # aggregated metrics; this toy app has nothing to persist, so it is deliberately not bound.
    strategy.start(
        grid=grid,
        initial_arrays=arrays,
        num_rounds=num_rounds,
    )
