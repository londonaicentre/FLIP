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

# Multi-model Ark+ Chest X-ray Evaluation (NVFLARE Client API) — FLIP tutorial

FLIP tutorial for federated evaluation of **multiple** Ark+ models (a zero-shot foundation model and a
fine-tuned model) on chest X-ray classification, using the **NVFLARE Client API**. The same hold-out data
at each site is scored against every model, and per-lesion AUROC plus pairwise **DeLong** statistical
comparisons between the models are reported.

This tutorial replaces the previous executor-based implementation (`class FLIP_EVALUATOR(Executor)` +
the bespoke `ModelEval`/`EvaluationPTModelLocator` server flow, where **all** checkpoints arrived
unwrapped from a single `DataKind.COLLECTION` DXO), which is now deprecated. Here `evaluator.py` is a
plain `nvflare.client` script (`flare.init/receive/send`) and the job is a Python `FlipEvalRecipe` driven
by `job.py` rather than a job-type template + Docker harness.

Because the DeLong comparison needs every model's per-sample scores on the *same* cohort at once — which
the stock per-model `validate` broadcast can't provide — each client loads **both** checkpoints from the
app's bundled `custom/` directory (`job.py` stages the `.pt` files into every site) and scores them in
one pass. The server's per-model broadcasts are used only as triggers; the results are computed once and
returned for each. The `evaluation_results.json` output contract (per-site, per-model metrics + DeLong)
is unchanged from the legacy tutorial.

> **Scope.** Because both checkpoints are bundled into the app, this tutorial targets the **local NVFLARE
> simulator / `uv` workflow** (`make run`). It is not wired for the production FL platform, where the FL
> API stages checkpoints server-side only and clients never receive the `.pt` files — for a
> platform-deployable single-model evaluation, see the
> [baseline tutorial](../arkplus_baseline_classification_evaluation/README.md).

## Compatible job type

This tutorial is designed for `JOB_TYPE=evaluation_client_api`.

## Prerequisites

- Python 3.12+
- A GPU (the Ark+ Swin-Large models run at 768×768) for a SimEnv run — both models are held on the
  device together, so this is more memory-hungry than the single-model baseline
- Access to the Ark+ foundation-model checkpoint (see checkpoint setup below); the fine-tuned checkpoint
  is downloaded automatically

## Dataset setup

This app expects a DECAF-formatted chest X-ray dataset with:

- A per-site CSV dataframe with `accession_id` and lesion labels
- Images organised under `<images_dir>/<accession_id>/...` (DICOM)

For local development, per-site paths are set in `.env.app`:

- `DEV_IMAGES_DIR` / `DEV_DATAFRAME` (single-site dev default)
- `SITE1_IMAGES_DIR` / `SITE1_DATAFRAME`, `SITE2_IMAGES_DIR` / `SITE2_DATAFRAME`
  (per-site, for the 2-client simulation)

### Per-site data in the simulator

Unlike the previous executor-based implementation (whose testing harness Docker-mounted each site's data
onto the `SITE_DATA` paths in `config.json`), the Client-API SimEnv runs **in-process with no Docker
mounts**. Per-site data is therefore selected inside the evaluator: it calls `flare.get_site_name()`
(`site-1`/`site-2`) and `app_files/data_utils.py` resolves the matching `SITE{N}_IMAGES_DIR` /
`SITE{N}_DATAFRAME` from `.env.app` (falling back to the single `DEV_*` paths). So `site-1` and `site-2`
score **different** hold-out sets. The `SITE_DATA` block in `config.json` is retained only as a legacy
fallback (its `/site-data/...` paths were the old container-mount targets).

### Simulator vs. real deployment

The per-site local paths are **simulator-only**. On a real federated client the fl-client runs with
`LOCAL_DEV=false`, and the data layer ignores the local paths entirely: the cohort dataframe comes from
`FLIP().get_dataframe(project_id, query)` and DICOMs from `FLIP().get_by_accession_number(...)` (the
trust's data-access-api / imaging-api). `project_id`/`query` are supplied by the FL job config —
`project_id` via the evaluator's `--project_id {project_id}` arg (substituted by the FLIP-API) and
`query` via the top-level `query` key of `config_fed_client.json` (read by `evaluator.load_query()`). The
switch is keyed on `LOCAL_DEV` in `app_files/data_utils.py` (`_is_local_dev`).

The cohort query for a real deployment is [`query.sql`](query.sql) — it selects the **hold-out** chest
X-ray set (`procedure_source_value = 'Chest X-ray (holdout)'`) and returns the seven lesion-label columns
both models are scored against.

## Checkpoint setup

The app evaluates two models, so it needs two clean `.pt` checkpoints in `app_files/`: the foundation
model `arkplus_pretrained_weights.pt` and the fine-tuned model `arkplus_finetuned_weights.pt`. Both are
loaded by every client's evaluator. `make run`/`make export` prepare both automatically (they run
`prepare-checkpoint` first).

### Foundation model (arkplus_pretrained)

The zero-shot Ark+ checkpoint is produced from the raw Ark6 training output
(`Ark6_swinLarge768_ep50.pth.tar`) in two steps — identical to the
[baseline tutorial](../arkplus_baseline_classification_evaluation/README.md):

1. **Fetch the raw checkpoint** (once):

   ```bash
   make download-raw-checkpoint
   ```

   This downloads it to the path given by `RAW_CHECKPOINT` in `.env.app` (default
   `models/Ark6_swinLarge768_ep50.pth.tar`, an app-relative path). If you already have the file, point
   `RAW_CHECKPOINT` at it instead. Access is via [this form](https://forms.gle/qkoDGXNiKRPTDdCe8).

2. **Prepare it** — done automatically by `make run`/`make export`, or on demand with
   `make prepare-checkpoint`, which converts the raw checkpoint into the clean
   `arkplus_pretrained_weights.pt` (a no-op if it already exists). The conversion script lives at
   `process_tools/preprocess_checkpoints.py` — see [process_tools/README.md](process_tools/README.md) for
   the extraction and key-remapping details.

### Fine-tuned model (arkplus_finetuned)

The fine-tuned checkpoint is downloaded automatically from HuggingFace by `make run`/`make export` if not
already present — no manual steps required. To use your own, set `FINETUNED_CHECKPOINT` in `.env.app` to a
URL or a local (absolute) path.

## App configuration

Default local development settings are in `.env.app`:

- `JOB_TYPE=evaluation_client_api`
- `RAW_CHECKPOINT=models/Ark6_swinLarge768_ep50.pth.tar`
- `FINETUNED_CHECKPOINT=` (empty → download the default fine-tuned model from HuggingFace)
- `DEV_IMAGES_DIR` / `DEV_DATAFRAME` and the per-site `SITE{1,2}_*` paths

Each model is defined in `app_files/arkplus_flat_models.py`, built by `app_files/models.py`
(`_build_arkplus_raw`), and registered in `config.json["models"]` (its `arkplus_config`, checkpoint file,
and head/label mapping). The mapping from a model's NIH-14 head outputs to the target DECAF lesions lives
in `app_files/data_utils.py` (`MAPPING_REGISTRY`).

`make export`/`make run` run `job.py` in the **flip-utils** environment with the `full` ML extra (the same
package set the `flare-fl-base` FL image installs) so a local run matches the deployed image.

## Run the tutorial

`job.py` drives the recipe in two modes.

```bash
make download-raw-checkpoint   # once: fetch the raw Ark6 foundation checkpoint into models/

# Export the complete NVFLARE job for review (no GPU needed)
make export                    # prepares both checkpoints, then writes ./fl_job/flip_evaluation/

# SimEnv local simulation (requires GPU + data + both checkpoints)
make run                       # prepares both checkpoints (if needed), then runs the simulator via `make sim`
```

The fine-tuned checkpoint is auto-downloaded from HuggingFace on the first prepare. To override either
source, set `RAW_CHECKPOINT` / `FINETUNED_CHECKPOINT` in `.env.app` (or pass on the CLI, e.g.
`make run FINETUNED_CHECKPOINT=/path/to/custom_finetuned.pt`).

Useful targets: `make prepare-checkpoint` (prepare both checkpoints only), `make clean` (removes `./fl_job`).

## Key files

- `app_files/evaluator.py`: the Client-API evaluation loop (load both models → score → per-lesion AUROC + DeLong).
- `app_files/arkplus_flat_models.py`: the `ArkSwinTransformer` model definition.
- `app_files/models.py`: model factory (`_build_arkplus_raw`, plus `get_model()` for the recipe's persistor).
- `app_files/metrics_utils.py`: AUROC and the DeLong pairwise test implementation.
- `app_files/data_utils.py`: data loading, DICOM parsing, label mappings, transforms, per-site resolution.
- `app_files/config.json`: per-model checkpoint/architecture mapping and evaluation settings.
- `job.py`: builds `FlipEvalRecipe`, bundles both checkpoints, and runs export / SimEnv.

## Output metrics

The evaluator returns **aggregate** (cohort-level) metrics only — per-lesion AUROC per model plus pairwise
DeLong p-values — collected by the server into `evaluation_results.json` keyed by site then model:

```json
{
    "site-1": {
        "arkplus_pretrained": {
            "auroc_Effusion": 0.85,
            "auroc_Consolidation": 0.79,
            "auroc_Infiltration": 0.72,
            "auroc_Lung Nodule or Mass": 0.81,
            "auroc_Pneumothorax": 0.88,
            "delong_p_values": {
                "Effusion": { "arkplus_pretrained": 1.0, "arkplus_finetuned": 0.03 },
                "Consolidation": { "arkplus_pretrained": 1.0, "arkplus_finetuned": 0.15 },
                "Infiltration": { "arkplus_pretrained": 1.0, "arkplus_finetuned": 0.42 },
                "Lung Nodule or Mass": { "arkplus_pretrained": 1.0, "arkplus_finetuned": 0.07 },
                "Pneumothorax": { "arkplus_pretrained": 1.0, "arkplus_finetuned": 0.51 }
            }
        },
        "arkplus_finetuned": {
            "auroc_Effusion": 0.88,
            "auroc_Consolidation": 0.76,
            "auroc_Infiltration": 0.74,
            "auroc_Lung Nodule or Mass": 0.84,
            "auroc_Pneumothorax": 0.90,
            "delong_p_values": {
                "Effusion": { "arkplus_pretrained": 0.03, "arkplus_finetuned": 1.0 },
                "Consolidation": { "arkplus_pretrained": 0.15, "arkplus_finetuned": 1.0 },
                "Infiltration": { "arkplus_pretrained": 0.42, "arkplus_finetuned": 1.0 },
                "Lung Nodule or Mass": { "arkplus_pretrained": 0.07, "arkplus_finetuned": 1.0 },
                "Pneumothorax": { "arkplus_pretrained": 0.51, "arkplus_finetuned": 1.0 }
            }
        }
    },
    "site-2": {
        "...": "..."
    }
}
```

(Values above are illustrative.)

### Fields

| Key | Type | Description |
|-----|------|-------------|
| `auroc_<Lesion>` | `float` | Area under the ROC curve for this lesion. Ranges `[0, 1]`; `NaN` if only one class is present in the ground truth. |
| `delong_p_values` | `dict[str, dict[str, float]]` | Pairwise DeLong p-values for each lesion, keyed first by lesion name then by the *other* model name. The diagonal (model vs. self) is hardcoded `1.0` as a sanity check; off-diagonal entries are the two-sided DeLong test. Only present when ≥ 2 models are configured. |

Per-sample (row-level) predictions are deliberately **not** produced or exported: a per-patient list would
leak the exact evaluation cohort size and be linkable to individual patients. (The previous executor-based
implementation wrote per-sample CSVs to the run dir; this tutorial omits them and returns aggregate
metrics only.)

## Model code & `timm` compatibility

`app_files/arkplus_flat_models.py` adapts the `ArkSwinTransformer` from the original Ark+ repository
([jlianglab/Ark](https://github.com/jlianglab/Ark)), which pins **`timm==0.5.4`**. The model's `forward`
is kept identical to the upstream version (`forward_features` → projector → `omni_heads`).

There is a subtle cross-version gotcha. In **timm 0.5.4**, `SwinTransformer.forward_features` pooled
internally — it ended with `AdaptiveAvgPool1d(1)` and returned a per-image `(B, C)` vector — so the Ark
`forward` never needed to pool. In **modern timm (1.x)** that global average pool was **moved out** of
`forward_features` (into `forward_head`), and `forward_features` now returns the *unpooled* spatial map
`(B, H, W, C)` (a 24×24 grid for a 768px Swin).

This tutorial runs on modern timm, and the upstream `forward` bypasses `forward_head` (it uses its own
`omni_heads`). Without an explicit pool the heads emitted a **per-location** grid of outputs instead of one
prediction per image — producing mis-shaped predictions and making AUROC fail with
`ValueError: multi_class must be in ('ovo', 'ovr')`.

**Fix.** `forward` (and `generate_embeddings`) now apply an explicit global-average-pool over the spatial
dims right after `forward_features`, restoring the timm 0.5.4 behaviour and matching the Swin head's
default `global_pool='avg'`:

```python
x = self.forward_features(x)
x = self._global_pool(x)   # mean over spatial dims; no-op if already (B, C)
```

**Verified equivalent.** Holding the backbone fixed, the explicit pool was compared against an exact
replica of timm 0.5.4's `AdaptiveAvgPool1d(1)` pooling: the pooled features and all head outputs were
**bit-for-bit identical** (`max |Δ| = 0.0`), since averaging over the flattened token sequence (`L`) equals
averaging over the `H×W` grid (`L = H×W`). The fix is also a no-op if a future `timm` returns an
already-pooled `(B, C)` tensor.

## Notes and troubleshooting

- DeLong p-values below machine epsilon are reported as `0.0`. The test is two-sided (H₀: AUC_a = AUC_b).
- `delong_p_values` is only emitted when at least 2 models are configured in `config.json["models"]`.
- Both models are loaded onto the GPU together; if you hit an out-of-memory error, reduce `BATCH_SIZE` in
  `config.json` (it defaults to 1) or evaluate on a smaller GPU-fitting cohort.
