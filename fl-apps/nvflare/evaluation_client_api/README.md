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

# Client API model evaluation

## Overview

This is the NVFLARE **Client API** evaluation job type (`JOB_TYPE=evaluation_client_api`). It evaluates
a single uploaded model across every site and reports aggregate metrics. It is the Client-API
counterpart of the legacy [`evaluation`](../evaluation/README.md) template, which pairs the
`RUN_EVALUATOR` executor with the bespoke `ModelEval` + `EvaluationPTModelLocator` (multi-model
`COLLECTION`) server flow.

Here the server reuses the same cross-site validation path as the
[`standard_client_api`](../standard_client_api/README.md) training template: `InitEvaluation` → `CrossSiteModelEval`
(with `submit_model` disabled, so only the server-provided model is validated). The model is sourced
by the single-model `EvaluationModelLocator` and broadcast to clients as one `FLModel`.

The base configs (`app/config/config_fed_server.json`, `app/config/config_fed_client.json`) and
`meta.json` are **recipe-generated** from `flip.nvflare.recipes.FlipEvalRecipe`. Do not hand-edit them —
regenerate via `recipe.py` after any recipe change and commit the result.

## What's the logic?

1. `InitEvaluation` reports evaluation start to the Central Hub, runs the client image-cleanup task,
   and validates that `config.json` declares the model(s) to evaluate.
2. `CrossSiteModelEval` loads the uploaded checkpoint via `EvaluationModelLocator` and broadcasts it to
   every site as a single `FLModel` (`validate` task).
3. Each site runs the user's `evaluator.py` via `InProcessClientAPIExecutor`, using the NVFLARE Client
   API `is_evaluate()` path (`flare.receive()` / `flare.send()`) to receive the model and return
   **aggregate-only** metrics (`DataKind.METRICS`).
4. `EvaluationJsonGenerator` collects the metrics into `evaluation_results.json`, and
   `PersistToS3AndCleanup` zips + uploads the run directory to S3.

There is **no `validator.py`** and no client model submission — the evaluator only scores the
server-provided model.

## Execution sequence

**Server — `config_fed_server.json` `workflows` (run in order):**

1. `flip.nvflare.controllers.InitEvaluation`
2. `flip.nvflare.controllers.CrossSiteModelEval` (`submit_model_task_name=""`)

**Client — `config_fed_client.json` `executors`:**

- `init_task`, `post_validation` → `flip.nvflare.components.CleanupImages`
- `validate` → `nvflare.app_common.executors.InProcessClientAPIExecutor`
- Event handler: `ClientEventHandler`

## What does the user upload?

The required files (see [`required_files.json`](./required_files.json)) are:

- `evaluator.py` — the Client-API evaluation loop. Receive the global model (`flare.receive()`), score
  it on the local cohort, and return aggregate metrics via `flare.send(FLModel(metrics=...))`.
- `models.py` — defines the model; `models.get_model` is what the server persistor instantiates.
- `config.json` — evaluation configuration consumed by the custom code (e.g. `num_classes`, the
  `models` checkpoint mapping, the cohort `query`).

Helper modules (e.g. `transforms.py`) and the model checkpoint are uploaded alongside these.

## Regenerating the committed configs

After any change to `FlipEvalRecipe` (in `flip-utils/flip/nvflare/recipes/`), regenerate the committed
JSONs by running from the `flip-utils` venv:

```bash
cd flip-utils && uv run --no-sync python - <<'PY'
import sys, types, torch, runpy
m = types.ModuleType("models"); m.get_model = lambda: torch.nn.Linear(1, 1); sys.modules["models"] = m
sys.argv = ["recipe.py", "--output", "../fl-apps/nvflare/evaluation_client_api"]
runpy.run_path("../fl-apps/nvflare/evaluation_client_api/recipe.py", run_name="__main__")
PY
```

Then commit all three updated files: `app/config/config_fed_server.json`,
`app/config/config_fed_client.json`, and `meta.json`.
