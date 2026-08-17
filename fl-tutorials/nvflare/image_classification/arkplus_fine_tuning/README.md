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

# Ark+ Fine-tuning — Chest X-ray Classification

FLIP tutorial for **federated fine-tuning** of an Ark+ Swin foundation model on chest X-ray
classification, using the NVFLARE **Client API** (`nvflare.client`). It replaces the earlier
Executor-based implementation that used to live here: the finetuning semantics are unchanged (frozen
backbone, fresh 5-class head, local teacher/student EMA), only the FL plumbing was ported from the
legacy Executor API to the Client API. The job is defined entirely in Python via
[`FlipFedAvgRecipe`](../../../../flip-utils/flip/nvflare/recipes/flip_fedavg_recipe.py) in
[`job.py`](job.py) — no hand-written server/client JSON.

## Compatible job type

`config.json["job_type"] = "standard"`. Each client runs [`app_files/trainer.py`](app_files/trainer.py)
as an in-process Client-API script (`flare.init()` → `flare.receive()`/`flare.send()` round loop) via
NVFLARE's `InProcessClientAPIExecutor`. There is **no `validator.py`** — the held-out validation folds
into the trainer's `flare.is_evaluate()` branch (server-driven cross-site evaluation).

## Target labels

The five DECAF chest X-ray lesions are predicted by a single classifier head:

```json
"LESIONS": {
  "0": "Effusion",
  "1": "Consolidation",
  "2": "Infiltration",
  "3": "Lung Nodule or Mass",
  "4": "Pneumothorax",
  "-1": "Lungs in normal arrangement"
}
```

`Lungs in normal arrangement` is a negative override: when it is positive, all lesion labels for that
row are treated as negative. Labels come from the per-site dataframe (see Dataset setup).

## Model & Ark+ integration

NVFLARE's persistor loads `models.get_model` from [`app_files/models.py`](app_files/models.py).
`get_model()` builds an `ArkSwinTransformer` (in [`app_files/arkplus_flat_models.py`](app_files/arkplus_flat_models.py))
sized from the `ARKPLUS` block, loads the backbone from the checkpoint with `LOAD_BACKBONE_ONLY=true`
(the heads start fresh), and wraps it in `ArkPlusNVFlareWrapper` (which adapts Ark+'s
`model(images, head_id) -> (features, logits)` to the `model(images) -> logits` interface, and exposes
`forward_with_features` for the teacher/student loop). The trainer freezes every parameter that is not
under `ark_model.omni_heads`, so only the classifier head trains.

### Teacher/student training

When `USE_TEACHER_STUDENT=true`, each client holds a trainable **student** (initialised from the
global weights) and a frozen EMA **teacher**. The per-step loss combines a BCE label loss with an MSE
feature-consistency loss between student and teacher embeddings, weighted by `CONSISTENCY_WEIGHT`.
Only the student weights are returned to NVFLARE; the teacher is never aggregated.

## Finetuning mechanisms

Three optimisations keep the frozen-backbone round-trip cheap. They are driven by keys in
`config.json` and are exercised **locally** (the recipe in `job.py` wires them) and **on deploy** (the
fl-server injects the equivalent filters from the same keys at job-assembly):

| Key | Effect | Component |
| --- | --- | --- |
| `SERVER_CHECKPOINT: "pretrained_weights.pt"` | The backbone-only checkpoint seeds the round-0 global model **server-side** and is broadcast to clients — it is never bundled into the client app. | `InitialCheckpointPTModelPersistor` |
| `AGGREGATE_ONLY_REGEX: "omni_heads"` | Clients return only the trainable head each round (not the ~759 MiB backbone). | `KeepOnlyVars` (client result filter) |
| _(same key)_ | After round 0 the server broadcasts only the head; clients rebuild the full model from it, so training code is unchanged and memory stays flat across rounds. | `TrimBroadcastVars` (server) + `ReconstructFullModel` (client) |

In the local simulator the backbone checkpoint is staged into the **server** custom dir only (see
`stage_app_files` in [`job.py`](job.py)); clients build a bare architecture and receive the backbone at
round 0.

## Configuration

Training settings live in [`app_files/config.json`](app_files/config.json): `GLOBAL_ROUNDS`,
`LOCAL_ROUNDS`, `LR_START`/`LR_END`, `VAL_SPLIT`/`SPLIT_SEED`, `BATCH_SIZE`, plus the `LESIONS`
and `ARKPLUS` blocks.

## Dataset setup

This app expects a DECAF-formatted chest X-ray dataset: a per-site CSV dataframe with `accession_id`
and the lesion-label columns, plus DICOM images.

The quickest path uses the published reference dataset on Hugging Face. From the repo root:

```bash
make -C fl-tutorials download-arkplus-finetuning-data   # fetch + lay out data/arkplus/site{1,2}/
make -C fl-tutorials run-tutorial TUTORIAL=arkplus_fine_tuning
```

`download-arkplus-finetuning-data` pulls the `site1`/`site2` training splits of
[`aicentreflip/tutorials-arkplus-cxr-classification`](https://huggingface.co/datasets/aicentreflip/tutorials-arkplus-cxr-classification)
and normalises them into `fl-tutorials/nvflare/data/arkplus/site{1,2}/` (gitignored), matching this
tutorial's `.env.app` defaults (`SITE{1,2}_IMAGES_DIR` / `SITE{1,2}_DATAFRAME`). Requires a GPU + the
`flare-fl-base` image to run the simulator. To use your own data instead, point the per-site
`.env.app` values at it.

- **Simulator (`LOCAL_DEV=true`):** the two clients (`site-1`, `site-2`) train on distinct local data.
  The trainer selects a client's data by `flare.get_site_name()`; `app_files/data_utils.py` resolves
  the paths with the precedence `SITE{N}_IMAGES_DIR`/`SITE{N}_DATAFRAME` env → the single-site `DEV_*`
  env. The per-site env vars are set in [`.env.app`](.env.app).
- **Real deployment (`LOCAL_DEV=false`):** the cohort dataframe comes from
  `FLIP().get_dataframe(project_id, query)` and DICOMs from `FLIP().get_by_accession_number(...)`.
  `project_id` is passed to the trainer as `--project_id`; the cohort `query` is read from
  `config_fed_client.json` (see [`query.sql`](query.sql)).

### Example images

One representative radiograph per target lesion, left to right in classifier-head index order
(`Effusion` = 0 … `Pneumothorax` = 4):

![Representative chest radiograph for each of the five target lesions](docs/dataset_examples.png)

The first four panels are drawn from the site-2 training split; the pneumothorax panel is drawn from
the site-2 holdout split. Regenerate the figure with
[`docs/make_dataset_figure.py`](docs/make_dataset_figure.py), pointing `DATA_ROOT` at the directory
holding the dataset split folders:

```bash
make dataset-figure DATA_ROOT=/path/to/balanced_synthetic_split
```

It runs in the tutorial-local env with the `docs` extra (`matplotlib` + `pydicom`). Before drawing,
it checks each panel's lesion against `config.json` and each accession's labels against its split's
cohort dataframe, so a mislabelled figure fails rather than ships.

## Checkpoint setup

`make run`/`make export` require the backbone checkpoint at `app_files/pretrained_weights.pt`, produced
from the raw Ark6 output (`Ark6_swinLarge768_ep50.pth.tar`) by `make prepare-checkpoint` (a no-op if it
already exists). Fetch the raw checkpoint with `make download-raw-checkpoint`, or request access via
[this form](https://forms.gle/qkoDGXNiKRPTDdCe8); see [`process_tools/README.md`](process_tools/README.md).

## How to run

```bash
# Local NVFLARE simulator (GPU + DECAF data + checkpoint). Prepares the checkpoint, then runs job.py.
make -C fl-tutorials run-tutorial TUTORIAL=arkplus_fine_tuning
#   ...or, from this directory:
make run RAW_CHECKPOINT=/path/to/Ark6_swinLarge768_ep50.pth.tar

# Export the full NVFLARE job under ./fl_job/flip_fedavg/ (no GPU needed)
make export
```

`NUM_ROUNDS` (default `3`) and `N_CLIENTS` (default `2`) parameterise both targets and propagate
through the tutorial harness:

```bash
make run NUM_ROUNDS=10                                                 # 10-round local simulation
make export NUM_ROUNDS=10 N_CLIENTS=3
make -C fl-tutorials run-tutorial TUTORIAL=arkplus_fine_tuning NUM_ROUNDS=10
```

or pass the flags directly using the recipe syntax (the Makefile's `uv` environment — flip-utils with
the `full` extra — matches the deployed FL image; a bare `uv run` resolves to an env without
torch/nvflare):

```bash
uv run --project ../../../../flip-utils --extra full python job.py --n_clients 3 --num_rounds 10
```

> **Local knob only.** `--num_rounds` governs local simulation and export. In production the FL API
> reads `GLOBAL_ROUNDS` from `config.json` at submit time and overrides whatever `job.py` baked into
> the exported config — deployed round counts come from `config.json`, never from these flags.
> (The standalone NVFLARE submit path, `make -C fl-services/nvflare submit`, is not wired — run
> locally via the simulator, or exercise the platform path through the FLIP UI / `make e2e_smoke`.)

## Round metrics & platform comparison

After a simulator run, `make round-metrics` parses the FLIP `ScatterAndGather` controller events
(`Round N started/finished`, `Start/End aggregation`, `Contribution ... ACCEPTED`) from the
simulator server log — the same lines `scripts/fl_round_metrics/extract_platform_round_timings.sh` pulls from CloudWatch for
a deployed run — and writes the same artefacts (`rounds.tsv`, `summary.md`, a timing boxplot) under
`round_metrics/` in this directory:

```bash
make run NUM_ROUNDS=50            # full-length local replica (GPU, hours; smoke-test with the default 3 first)
make round-metrics                # -> round_metrics/simulator-<workspace>-<timestamp>/
make round-metrics COMPARE=/path/to/platform/rounds.tsv   # adds a platform − simulator overhead table
```

`COMPARE` takes a `rounds.tsv` produced by `scripts/fl_round_metrics/extract_platform_round_timings.sh` for a platform run of
this same app; the summary then includes a side-by-side steady-state table (round duration,
aggregation time, inter-round gap), i.e. a baseline for the overhead the platform adds over bare
local training. Knobs: `WORKSPACE` (simulator workspace parsed for logs; defaults to `job.py`'s
`/tmp/nvflare/arkplus_finetuning`) and `METRICS_OUT` (output base directory).

> **Interpretation caveat.** Both simulated clients share one host and one GPU
> (`num_threads = num_clients`), so simulator round durations bundle GPU contention between the two
> clients; the platform − simulator round-duration delta in turn bundles WAN transfer, platform
> orchestration, and hardware differences. The aggregation and inter-round-gap rows are the clean
> comparables — the generated `summary.md` spells this out.

### One-command cross-site replication

For a collaborating site reproducing the baseline (no FLIP hub or AWS access needed), a single
target chains the whole experiment — download the HF tutorial data (~6.3 GB) and the Ark+
checkpoint if missing, run the full-length simulator replica, extract the metrics, and pack
everything to send back:

```bash
git clone <repo> && cd FLIP/fl-tutorials/nvflare/image_classification/arkplus_fine_tuning
make reproduce-overhead          # hours on GPU; produces arkplus_sim_experiment_<host>_<stamp>.zip here
```

The bundle contains the metrics artefacts (`rounds.tsv`, `summary.md`, boxplot), the simulator
logs (model files excluded), and a `provenance/` folder (`config.json`, `.env.app`,
`host_info.txt` with GPU/OS/git-commit) so runs from different sites can be compared like-for-like.

Prerequisites: Linux with an NVIDIA GPU + drivers, [`uv`](https://docs.astral.sh/uv/) on `PATH`
(the first run builds the flip-utils environment automatically), internet access (Hugging Face +
Dropbox), and ~15 GB free disk. On a multi-GPU host pick a device with
`CUDA_VISIBLE_DEVICES=<n> make reproduce-overhead`. Knobs: `OVERHEAD_ROUNDS` (default `50`, the deployed
run's `GLOBAL_ROUNDS`). If the simulation finished but packing failed (or you want to re-send),
`make package-overhead-bundle` re-bundles the last run without re-simulating.

## Key files

- [`job.py`](job.py): builds `FlipFedAvgRecipe` (with `aggregate_only_regex`), stages `app_files/`
  (checkpoint → server only), and runs SimEnv / exports the job.
- [`app_files/trainer.py`](app_files/trainer.py): Client-API training loop — frozen backbone,
  teacher/student EMA, AMP, per-lesion metrics via `SummaryWriter`; returns the weight DIFF.
- [`app_files/models.py`](app_files/models.py): `get_model()` factory + `ArkPlusNVFlareWrapper`.
- [`app_files/arkplus_flat_models.py`](app_files/arkplus_flat_models.py): `ArkSwinTransformer` + `build_omni_model`.
- [`app_files/data_utils.py`](app_files/data_utils.py): data loading, per-site resolution, DICOM parsing, transforms.
- [`app_files/config.json`](app_files/config.json): model, training, finetuning and per-site data settings.

There is **no `validator.py`** — validation runs in the trainer's `flare.is_evaluate()` branch.

## Dependency note

The Ark+ model imports `timm`. If the FLIP/NVFLARE runtime does not include `timm`, model construction
fails — add it to the runtime dependencies, or set `ARKPLUS.REQUIRE_ARKPLUS_IMPORT=false` in
`config.json` only for a non-Ark fallback smoke test.
