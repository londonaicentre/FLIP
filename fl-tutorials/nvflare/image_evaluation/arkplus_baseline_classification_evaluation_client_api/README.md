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

# Baseline Ark+ Chest X-ray Evaluation (NVFLARE Client API) — FLIP tutorial

FLIP tutorial for federated evaluation of a single zero-shot Ark+ foundation model on chest X-ray
classification, using the **NVFLARE Client API**. This is the Client-API counterpart of
[`../arkplus_baseline_classification_evaluation`](../arkplus_baseline_classification_evaluation) (which uses
the legacy `RUN_EVALUATOR` executor). The hold-out data at each site is scored against the model and
per-lesion AUROC is reported.

## How it differs from the legacy evaluation tutorial

| | Legacy (`evaluation`) | This tutorial (`evaluation_client_api`) |
|---|---|---|
| Client code | `evaluator.py` is a `class FLIP_EVALUATOR(Executor)` | `evaluator.py` is a plain `nvflare.client` script (`flare.init/receive/send`) |
| Server flow | bespoke `ModelEval` + `EvaluationPTModelLocator` (multi-model `COLLECTION`) | shared `CrossSiteModelEval` validate path + single-model `EvaluationModelLocator` |
| Job definition | job-type template + harness | a Python `FlipEvalRecipe` driven by `job.py` |
| Weights on the client | unwrapped from a `COLLECTION` DXO | arrive as `input_model.params` |

The recipe loads the uploaded checkpoint on the server and broadcasts it to every client as a single
`FLModel`; the client's `is_evaluate()` branch scores it on the local cohort and returns aggregate
per-lesion AUROC. The numerical pipeline (model, transforms, head inference, label mapping, AUROC) is
identical to the legacy tutorial's, so the reported metrics match — only the FL transport differs. The
`evaluation_results.json` output contract is unchanged.

## Compatible job type

This tutorial is designed for `JOB_TYPE=evaluation_client_api`.

## Prerequisites

- Python 3.12+
- A GPU (the Ark+ Swin-Large model runs at 768×768), plus the `flare-fl-base` image for a SimEnv run
- Access to the Ark+ foundation-model checkpoint (see checkpoint setup below)

## Dataset setup

This app expects a DECAF-formatted chest X-ray dataset with:

- A per-site CSV dataframe with `accession_id` and lesion labels
- Images organised under `<images_dir>/<accession_id>/...` (DICOM)

For local development, per-site paths are set in `.env.app`:

- `DEV_IMAGES_DIR` / `DEV_DATAFRAME` (single-site dev default)
- `SITE1_IMAGES_DIR` / `SITE1_DATAFRAME`, `SITE2_IMAGES_DIR` / `SITE2_DATAFRAME`
  (per-site, for the 2-client simulation)

### Per-site data in the simulator

Unlike the legacy tutorial (whose testing harness Docker-mounted each site's data onto the `SITE_DATA`
paths in `config.json`), the Client-API SimEnv runs **in-process with no Docker mounts**. Per-site data is
therefore selected inside the evaluator: it calls `flare.get_site_name()` (`site-1`/`site-2`) and
`app_files/data_utils.py` resolves the matching `SITE{N}_IMAGES_DIR` / `SITE{N}_DATAFRAME` from `.env.app`
(falling back to the single `DEV_*` paths). So `site-1` and `site-2` score **different** hold-out sets.

### Simulator vs. real deployment

The per-site local paths are **simulator-only**. On a real federated client the fl-client runs with
`LOCAL_DEV=false`, and the data layer ignores the local paths entirely: the cohort dataframe comes from
`FLIP().get_dataframe(project_id, query)` and DICOMs from `FLIP().get_by_accession_number(...)` (the
trust's data-access-api / imaging-api). `project_id`/`query` are supplied by the FL job config — `project_id`
via the evaluator's `--project_id {project_id}` arg (substituted by the FLIP-API) and `query` via the
top-level `query` key of `config_fed_client.json` (read by `evaluator.load_query()`). The switch is keyed on
`LOCAL_DEV` in `app_files/data_utils.py` (`_is_local_dev`).

## Checkpoint setup

The evaluation app needs the foundation-model checkpoint as a clean `.pt` file at
`app_files/arkplus_pretrained_weights.pt`. The **server** loads it (`EvaluationModelLocator`) and broadcasts
the weights to clients over the validate task — the clients never read the `.pt`. It is produced from the raw
Ark6 training output (`Ark6_swinLarge768_ep50.pth.tar`) in two steps:

1. **Fetch the raw checkpoint** (once):

   ```bash
   make download-raw-checkpoint
   ```

   This downloads `Ark6_swinLarge768_ep50.pth.tar` to the path given by `RAW_CHECKPOINT` in `.env.app`
   (default `models/Ark6_swinLarge768_ep50.pth.tar`, an app-relative path). If you already have the file,
   point `RAW_CHECKPOINT` at it instead. Access to the raw checkpoint is via
   [this form](https://forms.gle/qkoDGXNiKRPTDdCe8).

2. **Prepare (pre-process) it** — done automatically by `make run`/`make export`, or on demand:

   ```bash
   make prepare-checkpoint
   ```

   `prepare-checkpoint` is a no-op if `arkplus_pretrained_weights.pt` already exists; otherwise it converts
   the raw checkpoint into a clean state dict (runs in this tutorial's local `uv` env). The conversion script
   lives at `process_tools/preprocess_checkpoints.py` — see
   [process_tools/README.md](process_tools/README.md) for the extraction and key-remapping details.

## App configuration

Default local development settings are in `.env.app`:

- `JOB_TYPE=evaluation_client_api`
- `RAW_CHECKPOINT=models/Ark6_swinLarge768_ep50.pth.tar`
- `DEV_IMAGES_DIR` / `DEV_DATAFRAME` and the per-site `SITE{1,2}_*` paths
- `FLIP_PROJECT_ID` / `FLIP_QUERY` (injected into the recipe for SimEnv; ignored under `LOCAL_DEV`)

The model is defined in `app_files/arkplus_flat_models.py`, built by `app_files/models.py` (`get_model()`),
and registered in `config.json["models"]`. The mapping from the model's NIH-14 head outputs to the target
DECAF lesions lives in `app_files/data_utils.py` (`MAPPING_REGISTRY`).

`make export`/`make run` run `job.py` in the **flip-utils** environment with the `full` ML extra (the same
package set the `flare-fl-base` FL image installs) so a local run matches the deployed image.

## Run the tutorial

`job.py` drives the recipe in two modes.

```bash
make download-raw-checkpoint   # once: fetch the raw Ark6 checkpoint into models/

# Export the complete NVFLARE job for review or Docker deployment (no GPU needed)
make export                    # prepares the checkpoint, then writes ./fl_job/flip_evaluation/

# SimEnv local simulation (requires GPU + data + checkpoint)
make run                       # prepares the checkpoint (if needed), then runs the simulator via `make sim`
```

Useful targets: `make prepare-checkpoint` (convert the raw checkpoint only), `make clean` (removes `./fl_job`).

## Key files

- `app_files/evaluator.py`: the Client-API evaluation loop (receive model → score → send per-lesion AUROC).
- `app_files/arkplus_flat_models.py`: the `ArkSwinTransformer` model definition.
- `app_files/models.py`: model factory (`get_model()`).
- `app_files/metrics_utils.py`: AUROC and head→lesion label mapping.
- `app_files/data_utils.py`: data loading, DICOM parsing, label mappings, transforms, per-site resolution.
- `app_files/config.json`: model/checkpoint mapping and evaluation settings.
- `job.py`: builds `FlipEvalRecipe` and runs export / SimEnv.

## Output metrics

The evaluator returns **aggregate** (cohort-level) per-lesion AUROC only, collected by the server into
`evaluation_results.json` keyed by site then model:

```json
{
    "site-1": {
        "arkplus_pretrained": {
            "auroc_Effusion": 0.84,
            "auroc_Consolidation": 0.87,
            "auroc_Infiltration": 0.85,
            "auroc_Lung Nodule or Mass": 0.94,
            "auroc_Pneumothorax": 0.64
        }
    },
    "site-2": {
        "...": "..."
    }
}
```

(Values above are illustrative, from a sample local run.)

### Fields

| Key | Type | Description |
|-----|------|-------------|
| `auroc_<Lesion>` | `float` | Area under the ROC curve for this lesion. Ranges `[0, 1]`; `NaN` if only one class is present in the ground truth. |

Per-sample (row-level) predictions are deliberately **not** produced or exported: a per-patient list would
leak the exact evaluation cohort size and be linkable to individual patients. (The legacy tutorial wrote
per-sample CSVs to the run dir; the Client-API variant omits them.)

## Model code & `timm` compatibility

`app_files/arkplus_flat_models.py` adapts the `ArkSwinTransformer` from the original Ark+ repository
([jlianglab/Ark](https://github.com/jlianglab/Ark)), which pins **`timm==0.5.4`**. The model's `forward` is
kept identical to the upstream version, with one adaptation for modern timm: in **timm 0.5.4**,
`SwinTransformer.forward_features` pooled internally and returned a per-image `(B, C)` vector, whereas in
**timm 1.x** the global average pool moved into `forward_head` and `forward_features` returns the *unpooled*
spatial map `(B, H, W, C)`. Since the Ark `forward` bypasses `forward_head`, an explicit global-average-pool
is applied right after `forward_features` (a no-op if the tensor is already `(B, C)`), restoring the 0.5.4
behaviour. The pooled features and head outputs were verified bit-for-bit identical to a timm-0.5.4
`AdaptiveAvgPool1d(1)` replica. See the legacy tutorial's README for the full derivation.
