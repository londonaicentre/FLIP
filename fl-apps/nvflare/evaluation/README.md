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

This is the FLARE **Client API** evaluation job type (`JOB_TYPE=evaluation`). It evaluates
every uploaded model listed in `config.json['models']` across every site and reports aggregate
metrics.

The server reuses the cross-site validation path of the
[`standard`](../standard/README.md) training template (see [Execution sequence](#execution-sequence)).
Only server-provided models are validated; several models per job are supported (e.g. the Ark+
multimodel evaluation).

The base configs (`app/config/config_fed_server.json`, `app/config/config_fed_client.json`) and
`meta.json` are **recipe-generated** from `flip.nvflare.recipes.FlipEvalRecipe`. Do not hand-edit them —
see [Regenerating the committed configs](#regenerating-the-committed-configs).

## How it works

1. `InitEvaluation` reports evaluation start to the Central Hub, runs the client image-cleanup task,
   and validates that `config.json` declares the model(s) to evaluate.
2. `GlobalModelEval` loads each model listed in `config.json['models']` via `EvaluationModelLocator`
   and sends it to every site as its own `validate` task — one `FLModel` per (model, site).
3. Each site runs the user's `evaluator.py` via `ClientAPIExecutor` (in-process mode), using the FLARE Client
   API `is_evaluate()` path (`flare.receive()` / `flare.send()`) to receive the model and return
   **aggregate-only** metrics (`DataKind.METRICS`).
4. `EvaluationJsonGenerator` collects the metrics into `evaluation_results.json` and every failed
   `validate` task into `evaluation_failures.json`, and `PersistToS3AndCleanup` zips + uploads the
   run directory to S3.
5. At the end of the run `ServerEventHandler` sends each site's metrics to the Central Hub, so they
   show on the model page against that site (one point at round 0). Nested keys join with `/` and
   are upper-cased; when a site evaluated more than one model, each label starts with the model's
   name. Only finite numbers are sent: a NaN metric is left out, and lists are never sent.

### Metrics vs. supplementary results

- **`FLModel.metrics`** — sent to the Central Hub and plotted per site.
- **Anything else** — put it in `meta[FlipMetaKey.SUPPLEMENTARY_RESULTS.value]` (`FlipMetaKey` is in
  `flip.constants`). `EvaluationJsonGenerator` merges it, key by key, into the same site/model entry
  of `evaluation_results.json`, beside the metrics.

Example: the Ark+ multimodel evaluation returns its per-model AUROCs as `metrics` and its DeLong p/q
tables as supplementary results.

There is **no `validator.py`** and no client model submission — the evaluator only scores the
server-provided model.

## Outputs

Both files land in `evaluation_results/` inside the results zip.

`evaluation_results.json` is keyed by data site, then by the validated model's name (`GlobalModelEval`
sends one `validate` task per (model, client), so each model gets its own entry):

```json
{
  "Trust_1": {
    "SRV_arkplus_pretrained": { "auroc": 0.839 },
    "SRV_arkplus_finetuned": { "auroc": 0.871 }
  }
}
```

`evaluation_failures.json` lists every `validate` task that did not return metrics. It is always
written, so an empty list `[]` means no task failed:

```json
[{ "model": "SRV_arkplus_pretrained", "client": "Trust_1", "return_code": "TASK_ABORTED" }]
```

The results zip is uploaded in every case; it carries `evaluation_failures.json` and `error_log.txt`.

| Outcome | Final model status |
|---|---|
| Some or all `validate` tasks succeed | `RESULTS_UPLOADED` (failed tasks listed in `evaluation_failures.json`) |
| Every `validate` task fails | `ERROR` |

## Execution sequence

**Server — `config_fed_server.json` `workflows` (run in order):**

1. `flip.nvflare.controllers.InitEvaluation`
2. `nvflare.app_common.workflows.global_model_eval.GlobalModelEval`
3. `flip.nvflare.controllers.BroadcastTask`

**Client — `config_fed_client.json` `executors`:**

- `init_task`, `post_validation` → `flip.nvflare.components.CleanupImages`
- `validate` → `nvflare.app_common.executors.ClientAPIExecutor` (in-process mode)
- Event handler: `ClientEventHandler`

## What you upload

The required files (see [`required_files.json`](./required_files.json)) are:

- `evaluator.py` — the Client-API evaluation loop. Receive the global model (`flare.receive()`), score
  it on the local cohort, and return aggregate metrics via `flare.send(FLModel(metrics=...))`.
- `models.py` — defines the model; `models.get_model` is what the server persistor instantiates.
- `config.json` — evaluation configuration consumed by the custom code (e.g. `num_classes`, the
  `models` checkpoint mapping, the cohort `query`).

Helper modules (e.g. `transforms.py`) and the model checkpoint are uploaded alongside these.

## Regenerating the committed configs

After any change to `FlipEvalRecipe` (in `flip-utils/flip/nvflare/recipes/`), regenerate the committed
JSONs. Run this from the repository root; it uses the `flip-utils` environment and a stub `models` module
in place of a user's `models.py`:

```bash
cd flip-utils && uv run --no-sync python - <<'PY'
import sys, types, torch, runpy
m = types.ModuleType("models"); m.get_model = lambda: torch.nn.Linear(1, 1); sys.modules["models"] = m
sys.argv = ["recipe.py", "--output", "../fl-apps/nvflare/evaluation"]
runpy.run_path("../fl-apps/nvflare/evaluation/recipe.py", run_name="__main__")
PY
```

Then commit all three updated files: `app/config/config_fed_server.json`,
`app/config/config_fed_client.json`, and `meta.json`.
