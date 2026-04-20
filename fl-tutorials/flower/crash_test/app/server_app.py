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

"""crash_test: ServerApp running CrashTestStrategy.

The crashing ClientApp produces error replies; CrashTestStrategy feeds each
reply to handle_client_exception, which should transition the FLIP run status
to ERROR.
"""

import numpy as np
from flip import FLIP
from flwr.app import ArrayRecord, Context
from flwr.serverapp import Grid, ServerApp

from app.strategy import CrashTestStrategy

app = ServerApp()


@app.main()
def main(grid: Grid, context: Context, flip: FLIP = FLIP()) -> None:
    run_config = context.run_config
    model_id = run_config.get("flip-model-id", "uuid")
    num_rounds = int(run_config.get("num-server-rounds", 1))

    # A trivial ArrayRecord — the clients crash before touching the weights.
    arrays = ArrayRecord([np.zeros(1, dtype=np.float32)])

    strategy = CrashTestStrategy(flip=flip, model_id=model_id)
    strategy.start(grid=grid, initial_arrays=arrays, num_rounds=num_rounds)
