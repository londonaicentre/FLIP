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

# Evaluating a chest X-ray foundation model (Ark+)

FLIP tutorial for federated evaluation of a single zero-shot Ark+ foundation model on chest X-ray
classification, using the **FLARE Client API**. The hold-out data at each site is scored against the
model and per-lesion AUROC is reported.

`evaluator.py` is a plain `nvflare.client` script (`flare.init/receive/send`, weights arriving as
`input_model.params`). The job is a Python `FlipEvalRecipe` driven by `job.py`. The server loads the uploaded
checkpoint with a single-model `EvaluationModelLocator` (through the shared `CrossSiteModelEval` validate path)
and broadcasts it to every client as a single `FLModel`. The client's `is_evaluate()` branch scores it on the
local cohort and returns aggregate per-lesion AUROC, which ends up in `evaluation_results.json`.

## At a glance

| Item | Value |
|---|---|
| What you upload | everything in `app_files/`, plus the checkpoint from `make prepare-checkpoint` |
| Job type | evaluation |
| Imaging | on, DICOM to NIfTI off |
| Cohort query | `query.sql` (the hold-out set) |
| Dataset | `make -C fl-tutorials download-arkplus-eval-data`, plus the raw Ark+ checkpoint (`make download-raw-checkpoint`) |
| Simulator | `make -C fl-tutorials run-tutorial TUTORIAL=arkplus_baseline_classification_evaluation` (GPU; `MAX_SAMPLES=128` by default) |

Example radiographs, one per target lesion, are in the fine-tuning tutorial's
[`docs/dataset_examples.png`](../../image_classification/arkplus_fine_tuning/docs/dataset_examples.png)
(same dataset family; this tutorial scores the hold-out splits).

## Prerequisites

- Python 3.12+
- A GPU (the Ark+ Swin-Large model runs at 768×768) for a SimEnv run
- Access to the Ark+ foundation-model checkpoint (see checkpoint setup below)

## Dataset setup

This app expects a DECAF-formatted chest X-ray dataset with:

- A per-site CSV dataframe with `accession_id` and lesion labels
- Images organised under `<images_dir>/<accession_id>/...` (DICOM)

The quickest path uses the published reference dataset on Hugging Face. From the repo root:

```bash
make -C fl-tutorials download-arkplus-eval-data   # fetch + lay out data/arkplus/site{1,2}_holdoff/
# also fetch the raw Ark+ checkpoint first — see "Checkpoint setup" below (make download-raw-checkpoint)
make -C fl-tutorials run-tutorial TUTORIAL=arkplus_baseline_classification_evaluation
```

`download-arkplus-eval-data` pulls the `site1_holdoff`/`site2_holdoff` hold-out splits of
[`aicentreflip/tutorials-arkplus-cxr-classification`](https://huggingface.co/datasets/aicentreflip/tutorials-arkplus-cxr-classification)
and normalises them into `fl-tutorials/data/arkplus/site{1,2}_holdoff/` (gitignored), matching
this tutorial's `.env.app` defaults.

For local development, per-site paths are set in `.env.app`:

- `DEV_IMAGES_DIR` / `DEV_DATAFRAME` (single-site dev default)
- `SITE1_IMAGES_DIR` / `SITE1_DATAFRAME`, `SITE2_IMAGES_DIR` / `SITE2_DATAFRAME`
  (per-site, for the 2-client simulation)

### Per-site data in the simulator

The Client-API SimEnv runs **in-process with no Docker mounts**, so per-site data is selected inside the
evaluator: it calls `flare.get_site_name()` (`site-1`/`site-2`) and
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

The cohort query for a real deployment is [`query.sql`](query.sql) — it selects the **hold-out** chest
X-ray set (`procedure_source_value = 'Chest X-ray (holdout)'`) and returns the seven lesion-label columns.
Pass it as the project's cohort query (e.g. `make e2e_smoke QUERY_FILE=.../arkplus_baseline_classification_evaluation/query.sql`).

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

- `JOB_TYPE=evaluation`
- `RAW_CHECKPOINT=models/Ark6_swinLarge768_ep50.pth.tar`
- `DEV_IMAGES_DIR` / `DEV_DATAFRAME` and the per-site `SITE{1,2}_*` paths
- `FLIP_PROJECT_ID` / `FLIP_QUERY` (passed to the recipe, which writes them into the generated job config; the
  evaluator ignores them under `LOCAL_DEV`, where data comes from the local paths)

The model is defined in `app_files/arkplus_flat_models.py`, built by `app_files/models.py` (`get_model()`),
and registered in `config.json["models"]`. The mapping from the model's NIH-14 head outputs to the target
DECAF lesions lives in `app_files/data_utils.py` (`MAPPING_REGISTRY`).

`make export`/`make run` run `job.py` in the **flip-utils** environment with the `full` ML extra (the same
package set the `flare-fl-base` FL image installs) so a local run matches the deployed image.

## Run the tutorial

`job.py` drives the recipe in two modes. Run these targets from this tutorial's directory; the `make -C fl-tutorials …` targets above run from the repo root.

```bash
make download-raw-checkpoint   # once: fetch the raw Ark6 checkpoint into models/

# Export the complete FLARE job for review or Docker deployment (no GPU needed)
make export                    # prepares the checkpoint, then writes ./fl_job/flip_evaluation/

# SimEnv local simulation (requires GPU + data + checkpoint)
make run                       # prepares the checkpoint (if needed), then runs the simulator via `make sim`
```

`make run`/`make sim` score a deterministic, class-balanced subset of `MAX_SAMPLES` (default `128`)
hold-out studies per simulated site (`cap_dataframe` in `app_files/data_utils.py`), so a plain run is a
quick smoke test; `make run MAX_SAMPLES=0` (or empty) scores the full ~470 studies per site. The cap is
simulator-only: only the `LOCAL_DEV` dataframe path reads it, and a deployed job scores whatever cohort
the trust's data-access-api returns. `WORKSPACE` overrides the simulator workspace root.

Other targets: `make clean` (removes `./fl_job`); `make prepare-checkpoint` is covered under [Checkpoint setup](#checkpoint-setup).

## Key files

- `app_files/evaluator.py`: the Client-API evaluation loop (receive model → score → send per-lesion AUROC).
- `app_files/arkplus_flat_models.py`: the `ArkSwinTransformer` model definition.
- `app_files/models.py`: model factory (`get_model()`).
- `app_files/metrics_utils.py`: AUROC and applying the head→lesion label mapping (the mappings themselves are
  in `data_utils.py`).
- `app_files/data_utils.py`: data loading, DICOM parsing, label mappings, transforms, per-site resolution.
- `app_files/config.json`: model/checkpoint mapping and evaluation settings.
- `job.py`: builds `FlipEvalRecipe` and runs export / SimEnv.

## Output metrics

The evaluator returns **aggregate** (cohort-level) per-lesion AUROC only. The server collects it into
`evaluation_results.json`, keyed first by site and then by the server's validate task (`SRV_<model>`).
The FL server also sends the scores to the hub, where they appear on the model page per trust as
`AUROC_<LESION>`; a NaN AUROC (only one class present) is left out:

```json
{
    "site-1": {
        "SRV_arkplus_pretrained": {
            "auroc_Effusion": 0.998,
            "auroc_Consolidation": 0.989,
            "auroc_Infiltration": 0.863,
            "auroc_Lung Nodule or Mass": 0.970,
            "auroc_Pneumothorax": 0.971
        }
    },
    "site-2": {
        "...": "..."
    }
}
```

The values above are real scores from the pretrained checkpoint on one hold-out cohort: one cohort's result,
not a target to reproduce. A default simulator run (`MAX_SAMPLES=128`) is a smoke test, not a measurement;
see [Run the tutorial](#run-the-tutorial) for the full-cohort setting.

### Fields

| Key | Type | Description |
|-----|------|-------------|
| `auroc_<Lesion>` | `float` | Area under the ROC curve for this lesion. Ranges `[0, 1]`; `NaN` if only one class is present in the ground truth. |

Per-sample (row-level) predictions are deliberately **not** produced or exported: a per-patient list would
leak the exact evaluation cohort size and be linkable to individual patients.

## Model code & `timm` compatibility

`app_files/arkplus_flat_models.py` adapts the `ArkSwinTransformer` from the original Ark+ repository
([jlianglab/Ark](https://github.com/jlianglab/Ark)), which pins **`timm==0.5.4`**. The model's `forward` is
intentionally kept identical to the upstream version:

```python
x = self.forward_features(x)
if self.projector:
    x = self.projector(x)
return x, self.omni_heads[head_n](x)
```

There is a subtle cross-version gotcha here. In **timm 0.5.4**, `SwinTransformer.forward_features` pooled
internally — it ended with `AdaptiveAvgPool1d(1)` and returned a per-image `(B, C)` vector — so the Ark
`forward` never needed to pool. In **modern timm (1.x)**, that global average pool was **moved out** of
`forward_features` (it now lives in `forward_head`), and `forward_features` returns the *unpooled* spatial
feature map `(B, H, W, C)` (a 24×24 grid for a 768px Swin).

This tutorial runs on modern timm, and the upstream `forward` bypasses `forward_head` (it uses its own
`omni_heads`). Without an explicit pool the heads emit a **per-location** grid of outputs instead of one
prediction per image, and AUROC fails with `ValueError: multi_class must be in ('ovo', 'ovr')`.

So `forward` (and `generate_embeddings`) apply an explicit global-average-pool over the spatial dims right
after `forward_features`, restoring the timm 0.5.4 behaviour and matching the Swin head's default
`global_pool='avg'`:

```python
x = self.forward_features(x)
x = self._global_pool(x)   # mean over spatial dims; no-op if already (B, C)
```

**Verified equivalent.** Holding the backbone fixed, the explicit pool was compared against an exact
replica of timm 0.5.4's `AdaptiveAvgPool1d(1)` pooling. The pooled features and all head outputs were
**bit-for-bit identical** (`max |Δ| = 0.0`) — averaging over the flattened sequence of tokens (`L`) is the
same operation as averaging over the `H×W` grid (`L = H×W`). The fix is also shape-robust: it is a no-op
if a future `timm` returns an already-pooled `(B, C)` tensor.
