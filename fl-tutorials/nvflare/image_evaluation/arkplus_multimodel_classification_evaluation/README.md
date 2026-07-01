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

# Multi-model Ark+ Chest X-ray Evaluation — FLIP tutorial

FLIP tutorial for federated evaluation of multiple Ark+ models (zero-shot
foundation and fine-tuned) on chest X-ray classification.  The same hold-out
data is evaluated against every model in the job, and per-lesion AUROC plus
pairwise DeLong statistical comparisons are reported.

## Compatible job type

This tutorial is designed for `JOB_TYPE=evaluation`.

## Prerequisites

- Python 3.12+
- Docker + Docker Compose
- `fl-tutorials/nvflare/testing/.env.testing` configured (at minimum
  `FL_BASE_IMAGE_TAG`, `NUM_CLIENTS`)
- Access to the Ark+ model checkpoints (see checkpoint setup below)

## Dataset setup

This app expects a DECAF-formatted chest X-ray dataset with:

- A per-site CSV dataframe with `accession_id` and lesion labels
- Images organised as `<images_dir>/<accession_id>/<file>.jpg.png` or similar

The dataset is configured through the `SITE_DATA` section in `app_files/config.json`.
For local development, paths are also settable via `.env.app`:

- `DEV_IMAGES_DIR=<path>`
- `DEV_DATAFRAME=<path>`

### Simulator vs. real deployment

`SITE_DATA` and the `/site-data/site-N/...` mounts are **simulator-only**. The NVFLARE
simulator runs every client (`site-1`, `site-2`) inside one container, so per-site data must
be given a distinct mount target selected by client name — this is local development only.

On a real federated client the fl-client runs with `LOCAL_DEV=false`, and the data layer
ignores `SITE_DATA` entirely: the cohort dataframe comes from
`FLIP().get_dataframe(project_id, query)` and DICOMs from `FLIP().get_by_accession_number(...)`
(the trust's data-access-api / imaging-api), reading downloaded images from the single shared
`/app/data/images` mount — there are no per-site mounts. `project_id`/`query` are supplied by
the FL job config (`config_fed_client.json` → `RUN_EVALUATOR`). The switch is keyed on
`LOCAL_DEV` in `app_files/data_utils.py` (`_is_local_dev`), mirroring the flip package's own
`FLIPStandardDev`/`FLIPStandardProd` selection.

## Checkpoint setup

The app evaluates two models, so it needs two checkpoints in `app_files/` (both
`.pt`): the foundation model `arkplus_pretrained_weights.pt` and the fine-tuned
model `arkplus_finetuned_weights.pt`. `make run` prepares both automatically (it
runs `prepare-checkpoint` first).

### Foundation model (arkplus_pretrained)

The zero-shot Ark+ checkpoint is produced from the raw Ark6 training output
(`Ark6_swinLarge768_ep50.pth.tar`) in two steps — identical to the
[baseline tutorial](../arkplus_baseline_classification_evaluation/README.md):

1. **Fetch the raw checkpoint** (once):

   ```bash
   make download-raw-checkpoint
   ```

   This downloads it to the path given by `RAW_CHECKPOINT` in `.env.app` (default
   `models/Ark6_swinLarge768_ep50.pth.tar`, an app-relative path). If you already
   have the file, point `RAW_CHECKPOINT` at it instead. Access is via
   [this form](https://forms.gle/qkoDGXNiKRPTDdCe8).

2. **Prepare it** — done automatically by `make run`, or on demand with
   `make prepare-checkpoint`, which converts the raw checkpoint into the clean
   `arkplus_pretrained_weights.pt` (a no-op if it already exists).

### Fine-tuned model (arkplus_finetuned)

The fine-tuned checkpoint is downloaded automatically from HuggingFace by
`make run` if not already present — no manual steps required. To use your own,
set `FINETUNED_CHECKPOINT` in `.env.app` to a URL or a local (absolute) path.

### Pre-processing internals

The conversion script lives at `process_tools/preprocess_checkpoints.py` — see
[process_tools/README.md](process_tools/README.md) for details on the
extraction and key-remapping logic.

## App configuration

Default local development settings are in `.env.app`:

- `JOB_TYPE=evaluation`
- `RAW_CHECKPOINT=models/Ark6_swinLarge768_ep50.pth.tar`
- `FINETUNED_CHECKPOINT=` (empty → download the default fine-tuned model from HuggingFace)
- `DEV_IMAGES_DIR` / `DEV_DATAFRAME` and the per-site `SITE{1,2}_*` paths

Model definitions live in `app_files/models.py` and are registered in
`config.json["models"]` via the path key (e.g. `arkplus_multihead`).

## Run the tutorial

```bash
make download-raw-checkpoint   # once: fetch the raw Ark6 foundation checkpoint
make run                       # prepares both checkpoints (if needed), then evaluates
```

The fine-tuned checkpoint is auto-downloaded from HuggingFace on the first run. To
override either source, set `RAW_CHECKPOINT` / `FINETUNED_CHECKPOINT` in `.env.app`
(or pass on the CLI, e.g. `make run FINETUNED_CHECKPOINT=/path/to/custom_finetuned.pt`).

Useful targets:

- `make prepare-checkpoint`: prepare both checkpoints only (no run)
- `make shell`: interactive shell in the simulator container
- `make down`: stop the simulator service
- `make clean`: remove generated simulator artifacts

## Key evaluation files

- `app_files/evaluator.py`: evaluation loop, per-model inference, metrics + DeLong
- `app_files/models.py`: model factory (`model_paths` dict)
- `app_files/metrics_utils.py`: AUROC and DeLong pairwise test implementation
- `app_files/data_utils.py`: data loading, DICOM parsing, label mappings
- `app_files/transforms.py`: per-model inference transforms
- `app_files/config.json`: model/checkpoint mapping and evaluation settings

## Evaluation output

Each client returns a DXO of kind `METRICS` whose data dict is saved by the
server into `evaluation_results.json` under the client's site name.  There is
no required output schema — `evaluator.py` may return any JSON-serialisable
structure.  The multi-model Ark+ evaluator returns per-model metrics and
pairwise DeLong statistical test results.

### Example `evaluation_results.json`

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
                "Effusion": {
                    "arkplus_pretrained": 1.0,
                    "arkplus_finetuned": 0.03
                },
                "Consolidation": {
                    "arkplus_pretrained": 1.0,
                    "arkplus_finetuned": 0.15
                },
                "Infiltration": {
                    "arkplus_pretrained": 1.0,
                    "arkplus_finetuned": 0.42
                },
                "Lung Nodule or Mass": {
                    "arkplus_pretrained": 1.0,
                    "arkplus_finetuned": 0.07
                },
                "Pneumothorax": {
                    "arkplus_pretrained": 1.0,
                    "arkplus_finetuned": 0.51
                }
            }
        },
        "arkplus_finetuned": {
            "auroc_Effusion": 0.88,
            "auroc_Consolidation": 0.76,
            "auroc_Infiltration": 0.74,
            "auroc_Lung Nodule or Mass": 0.84,
            "auroc_Pneumothorax": 0.90,
            "delong_p_values": {
                "Effusion": {
                    "arkplus_pretrained": 0.03,
                    "arkplus_finetuned": 1.0
                },
                "Consolidation": {
                    "arkplus_pretrained": 0.15,
                    "arkplus_finetuned": 1.0
                },
                "Infiltration": {
                    "arkplus_pretrained": 0.42,
                    "arkplus_finetuned": 1.0
                },
                "Lung Nodule or Mass": {
                    "arkplus_pretrained": 0.07,
                    "arkplus_finetuned": 1.0
                },
                "Pneumothorax": {
                    "arkplus_pretrained": 0.51,
                    "arkplus_finetuned": 1.0
                }
            }
        }
    },
    "site-2": {
        ...
    }
}
```

### Fields

| Key | Type | Description |
|-----|------|-------------|
| `auroc_<Lesion>` | `float` | Area under the ROC curve for this lesion. Ranges `[0, 1]`; `NaN` if only one class present in the ground truth. |
| `delong_p_values` | `dict[str, dict[str, float]]` | Pairwise DeLong p-values for each lesion, keyed first by lesion name then by the *other* model name. The diagonal (model vs. self) is hardcoded as `1.0` as a sanity check; off-diagonal entries are derived from the two-sided DeLong test. |

### Per-model output files

In addition to `evaluation_results.json`, the evaluator writes CSV outputs to
`manual_save_eval_results/` in the job workspace:

- `<model_name>_predictions.csv` — per-sample predictions and ground-truth labels
- `per_model_metrics.csv` — flat table of model, label, AUROC
- `delong_results.csv` — all DeLong pairwise comparisons in row format
  (`model_a`, `model_b`, `label`, `auc_a`, `auc_b`, `z_statistic`, `p_value`)

## Model code & `timm` compatibility

`app_files/arkplus_flat_models.py` adapts the `ArkSwinTransformer` from the
original Ark+ repository ([jlianglab/Ark](https://github.com/jlianglab/Ark)),
which pins **`timm==0.5.4`**. The model's `forward` is kept identical to the
upstream version (`forward_features` → projector → `omni_heads`).

There is a subtle cross-version gotcha. In **timm 0.5.4**,
`SwinTransformer.forward_features` pooled internally — it ended with
`AdaptiveAvgPool1d(1)` and returned a per-image `(B, C)` vector — so the Ark
`forward` never needed to pool. In **modern timm (1.x)** that global average pool
was **moved out** of `forward_features` (into `forward_head`), and
`forward_features` now returns the *unpooled* spatial map `(B, H, W, C)`
(a 24×24 grid for a 768px Swin).

This tutorial runs on modern timm, and the upstream `forward` bypasses
`forward_head` (it uses its own `omni_heads`). Without an explicit pool the heads
emitted a **per-location** grid of outputs instead of one prediction per image —
producing mis-shaped predictions and making AUROC fail with
`ValueError: multi_class must be in ('ovo', 'ovr')`.

**Fix.** `forward` (and `generate_embeddings`) now apply an explicit
global-average-pool over the spatial dims right after `forward_features`,
restoring the timm 0.5.4 behaviour and matching the Swin head's default
`global_pool='avg'`:

```python
x = self.forward_features(x)
x = self._global_pool(x)   # mean over spatial dims; no-op if already (B, C)
```

**Verified equivalent.** Holding the backbone fixed, the explicit pool was
compared against an exact replica of timm 0.5.4's `AdaptiveAvgPool1d(1)` pooling:
the pooled features and all head outputs were **bit-for-bit identical**
(`max |Δ| = 0.0`), since averaging over the flattened token sequence (`L`) equals
averaging over the `H×W` grid (`L = H×W`). The fix is also a no-op if a future
`timm` returns an already-pooled `(B, C)` tensor.

## Notes and troubleshooting

- DeLong p-values below machine epsilon are reported as `0.0`. The test is
  two-sided (H₀: AUC_a = AUC_b).
- If `delong_results.csv` is missing, confirm at least 2 models are configured
  in `config.json["models"]`.
