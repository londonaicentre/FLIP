<!--
    Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at
        http://www.apache.org/licenses/LICENSE-2.0
    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.
-->

# Client API federated training (FedAvg)

## Overview

This is the NVFLARE **Client API** federated-training job type (`JOB_TYPE=standard`). It performs a
**Federated Averaging** round-trip, driving client training through the NVFLARE Client API
(`InProcessClientAPIExecutor`). It replaced the retired Executor-based `standard` template, which
drove the clients through the legacy `RUN_TRAINER`/`RUN_VALIDATOR` executor pair.
The server side is: `InitTraining` → `ScatterAndGather` → stock `GlobalModelEval` → `BroadcastTask` cleanup.

The base configs (`app/config/config_fed_server.json`, `app/config/config_fed_client.json`) and
`meta.json` are **recipe-generated** from `flip.nvflare.recipes.FlipFedAvgRecipe`. Do not
hand-edit them — regenerate via `recipe.py` after any recipe change and commit the result.

## What's the logic?

For each global round (`num_rounds` in the recipe / `ScatterAndGather` args):

1. The server sends the current global model to every site.
2. Each site runs the user's `trainer.py` via `InProcessClientAPIExecutor`, using the NVFLARE
   Client API (`flare.receive()` / `flare.send()`) to receive and return model params.
3. Sites return their update as a weight **diff** — `FLModel(params=<local minus global>,
   params_type="DIFF")` → `DataKind.WEIGHT_DIFF` (a `WEIGHTS` return is rejected by the
   aggregator) — filtered through `PercentilePrivacy`.
4. The server averages the diffs with `InTimeAccumulateWeightedAggregator` (weighted by samples),
   `FullModelShareableGenerator` adds the averaged diff onto the global model, and
   `PTFileModelPersistor` persists it.

There is **no `validator.py`** — validation is orchestrated server-side via `GlobalModelEval` and
`ValidationJsonGenerator`. The `PercentilePrivacy` filter applies on training task results only.

## Execution sequence

**Server — `config_fed_server.json` `workflows` (run in order):**

1. `controller` — `flip.nvflare.controllers.InitTraining`
2. `controller1` — `flip.nvflare.controllers.ScatterAndGather`
3. `controller2` — stock `nvflare.app_common.workflows.global_model_eval.GlobalModelEval`
4. `controller3` — `flip.nvflare.controllers.BroadcastTask`

**Client — `config_fed_client.json` `executors` / `filters`:**

- `init_training`, `post_validation` → `flip.nvflare.components.CleanupImages`
- `train`, `validate` → `nvflare.app_common.executors.InProcessClientAPIExecutor`
- `train` result → `flip.nvflare.components.PercentilePrivacy` (DP noise filter)
- Event handlers: `ClientEventHandler`, `FlipAnalyticsBridge`

Post-training evaluation sends only the aggregated global model to each trust. The default template does not
request or redistribute client-local models; callers constructing `FlipFedAvgRecipe` directly can explicitly
set `submit_model_task_name="submit_model"` to restore full cross-site evaluation.

## What does the user upload?

The required files (see [`required_files.json`](./required_files.json)) are:

- `trainer.py` — the local training loop using the NVFLARE Client API (`flare.receive()` /
  `flare.send()`). Return the update as a diff: `FLModel(params=<local minus global>,
  params_type="DIFF")` → `DataKind.WEIGHT_DIFF`.
- `models.py` — defines the model; `models.get_model` is what the server persistor instantiates.
- `config.json` — model/training configuration consumed by the custom code (e.g. hyper-parameters,
  the cohort `query`).

Note: there is no `validator.py` for this job type — validation runs server-side.

## Regenerating the committed configs

After any change to `FlipFedAvgRecipe` (in `flip-utils/flip/nvflare/recipes/`), regenerate the
committed JSONs by running from the `flip-utils` venv:

```bash
cd flip-utils && uv run --no-sync python - <<'PY'
import sys, types, torch, runpy
m = types.ModuleType("models"); m.get_model = lambda: torch.nn.Linear(1, 1); sys.modules["models"] = m
sys.argv = ["recipe.py", "--output", "../fl-apps/nvflare/standard"]
runpy.run_path("../fl-apps/nvflare/standard/recipe.py", run_name="__main__")
PY
```

Then commit all three updated files: `app/config/config_fed_server.json`,
`app/config/config_fed_client.json`, and `meta.json`.
