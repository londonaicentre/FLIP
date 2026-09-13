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

# Federated evaluation (Flower)

## Overview

The Flower evaluation job type (`job_type=evaluation`): loads a **pre-trained checkpoint** and
scores it across every connected client — no training rounds, no parameter updates. The server
side is [`EvaluationStrategy`](app/strategy.py), a thin subclass of
[`FlipFedAvg`](../../../flip-utils/flip/flower/strategy.py) run with `fraction_train=0.0,
fraction_evaluate=1.0`; `FedAvg` itself aggregates whatever metrics the clients return (a weighted
average by `num-examples`), and the subclass adds only a per-client breakdown for the results
artifact. This is the Flower counterpart of the NVFLARE
[`evaluation`](../../nvflare/evaluation/README.md) job type, though the two differ in shape: NVFLARE
evaluates one or more **server-listed checkpoints** (`config.json['models']`) via a dedicated
`EvaluationModelLocator`, while this template evaluates the **single checkpoint staged for the
run** and reports aggregate + per-client metrics natively through `FedAvg`.

## What's the logic?

[`app/server_app.py`](app/server_app.py) (`@app.main()`):

1. Reports `INITIATED`, then loads the checkpoint named by the `checkpoint` run-config key from
   `flip-job-dir` — the app directory on the shared volume that the FL API stages the uploaded
   bundle (sources + checkpoint) into at submission time. The checkpoint is loaded strictly
   (`load_state_dict(..., strict=True)`) onto CPU: a mismatch between the checkpoint and
   `get_model()`'s architecture fails loudly rather than evaluating a partially-random model.
2. Packs the loaded weights into an `ArrayRecord` and starts `EvaluationStrategy` for
   `num-server-rounds` (default 1) rounds — `fraction_train=0.0` so no round trains,
   `fraction_evaluate=1.0` so every connected client evaluates.
3. Collects `strategy.per_client_results` into `evaluation_results.json` under
   `WORKING_DIR/{model_id}/evaluation_outputs/`, uploads it to S3 via `flip.upload_results_to_s3`,
   and reports `RESULTS_UPLOADED` (or `ERROR` on a save/upload failure).
4. Cleans up the job folder (the parent of `flip-job-dir`) once done, guarded by checking the
   `model_id` appears in the path before deleting anything.

[`app/strategy.py`](app/strategy.py)'s `EvaluationStrategy.aggregate_evaluate` records each reply's
`MetricRecord` (excluding the `num-examples` weighting key) into `per_client_results` keyed by
`client_name`, then delegates to `FlipFedAvg` for the actual weighted aggregation and Central Hub
forwarding (status, per-client metrics/exceptions, round events). There is no metric declaration or
validation anywhere in this template — whatever `client_app.py` returns in its `MetricRecord` is
what gets aggregated and reported.

## What does the user upload?

The required files (see [`required_files.json`](./required_files.json)) are:

- `client_app.py` — the Flower `ClientApp`, defining `@app.evaluate` (an `@app.train` handler is
  unused by this job type but is not itself forbidden).
- `config.json` — declares `job_type`. The Central Hub reads this key and nothing else, to pick
  which base application to bundle; run configuration lives in `config.toml`, not here.
- `models.py` — defines the model; `models.get_model` is what `server_app.py` instantiates before
  loading the checkpoint's weights into it.

The model checkpoint itself (e.g. `model.pt`) is uploaded alongside these and staged into the app
directory by the FL API; its filename is supplied per run via the `checkpoint` run-config key.

`app/server_app.py`, `app/strategy.py` and `pyproject.toml` ship with the template. Base files win
over uploaded ones — [`bundle_flower_application`](../../../flip-api/src/flip_api/fl_services/services/fl_service.py)
skips any uploaded file whose name collides with a base file — so a user cannot replace the server
app, the strategy, or the project config.

## Run-config keys

Declared under `[tool.flwr.app.config]` in [`pyproject.toml`](pyproject.toml) (placeholders here;
overridden per run via `config.toml`):

| Key | Purpose |
| --- | --- |
| `num-server-rounds` | Evaluation rounds (default `1` — evaluation only needs one pass). |
| `checkpoint` | Filename of the checkpoint to evaluate, relative to `flip-job-dir`. Dummy placeholder in `pyproject.toml`; the real value is injected per run. |
| `flip-model-id` / `flip-project-id` / `flip-cohort-query` / `flip-job-dir` | FLIP plumbing injected by the FL API at submission time. |
| `flip-min-clients` | Quorum for the run; the FL API overrides the placeholder with the participating-trust count at submit time (`flwr` rejects a `--run-config` key the app config does not declare). A malformed or zero value raises out of `min_clients_from_run_config` / the `EvaluationStrategy` constructor and the model is marked `ERROR`. |
| `local-epochs`, `learning-rate`, `batch-size`, `spatial-dims`, `num-classes` | Placeholders for model/training config consumed by user code; unused by this template's own server logic. |

## Local test

There is no server-side simulator run for this template on its own — it needs a real checkpoint and
connected clients. The shipped consumer, `fl-tutorials/flower/3d_spleen_segmentation_evaluation`,
is explicitly **not** run via `flwr run` / the Simulation Engine (see its README for the reasons —
a long-lived `flower-superlink` caching stale env, `ClientApp` running from a snapshot directory,
and FLIP's `DevSettings` singleton pinning `LOCAL_DEV` at import time). Instead:

```bash
make -C fl-tutorials/flower/3d_spleen_segmentation_evaluation download-checkpoints  # fetch model.pt
make -C fl-services/flower build   # build the fl-base / superlink / supernode images
make -C fl-services/flower up      # start fl-api, superlink, supernode-1, supernode-2
make -C fl-services/flower submit APP=3d_spleen_segmentation_evaluation
```

See [`fl-tutorials/flower/3d_spleen_segmentation_evaluation/README.md`](../../../fl-tutorials/flower/3d_spleen_segmentation_evaluation/README.md)
for the full walkthrough, including how to change which metrics are reported (edit the client's own
metric registry — nothing here needs to change) and how the dev compose stack wires
`DEV_DATAFRAME` / `DEV_IMAGES_DIR` / `WORKING_DIR` into the containers.
