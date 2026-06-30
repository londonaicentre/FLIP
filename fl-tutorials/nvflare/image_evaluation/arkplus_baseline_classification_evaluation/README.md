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

# Baseline Ark+ Chest X-ray Evaluation — FLIP tutorial

FLIP tutorial for federated evaluation of a single zero-shot Ark+ foundation
model on chest X-ray classification. The hold-out data at each site is scored
against the model and per-lesion AUROC is reported.

## Compatible job type

This tutorial is designed for `JOB_TYPE=evaluation`.

## Prerequisites

- Python 3.12+
- Docker + Docker Compose
- A GPU (the Ark+ Swin-Large model runs at 768×768)
- `fl-tutorials/nvflare/testing/.env.testing` configured (at minimum
  `FL_BASE_IMAGE_TAG`, `NUM_CLIENTS`)
- Access to the Ark+ foundation-model checkpoint (see checkpoint setup below)

## Dataset setup

This app expects a DECAF-formatted chest X-ray dataset with:

- A per-site CSV dataframe with `accession_id` and lesion labels
- Images organised under `<images_dir>/<accession_id>/...` (DICOM)

The dataset is configured through the `SITE_DATA` section in
`app_files/config.json`. For local development, paths are also settable via
`.env.app`:

- `DEV_IMAGES_DIR` / `DEV_DATAFRAME` (single-site dev default)
- `SITE1_IMAGES_DIR` / `SITE1_DATAFRAME`, `SITE2_IMAGES_DIR` / `SITE2_DATAFRAME`
  (per-site, for a 2-client simulation)

## Checkpoint setup

The evaluation app needs the foundation-model checkpoint as a clean `.pt` file
at `app_files/arkplus_pretrained_weights.pt`. This is produced from the raw Ark6
training output (`Ark6_swinLarge768_ep50.pth.tar`) in two steps:

1. **Fetch the raw checkpoint** (once):

   ```bash
   make download-raw-checkpoint
   ```

   This downloads `Ark6_swinLarge768_ep50.pth.tar` to the path given by
   `RAW_CHECKPOINT` in `.env.app` (default `models/Ark6_swinLarge768_ep50.pth.tar`,
   an app-relative path). If you already have the file, point `RAW_CHECKPOINT` at
   it instead. Access to the raw checkpoint is via
   [this form](https://forms.gle/qkoDGXNiKRPTDdCe8).

2. **Prepare (pre-process) it** — done automatically by `make run`, or on demand:

   ```bash
   make prepare-checkpoint
   ```

   `prepare-checkpoint` is a no-op if `arkplus_pretrained_weights.pt` already
   exists; otherwise it converts the raw checkpoint into a clean state dict that
   passes `EvaluationPTModelLocator`'s `strict=True` validation. The conversion
   script lives at `process_tools/preprocess_checkpoints.py` — see
   [process_tools/README.md](process_tools/README.md) for the extraction and
   key-remapping details.

## App configuration

Default local development settings are in `.env.app`:

- `JOB_TYPE=evaluation`
- `RAW_CHECKPOINT=models/Ark6_swinLarge768_ep50.pth.tar`
- `DEV_IMAGES_DIR` / `DEV_DATAFRAME` and the per-site `SITE{1,2}_*` paths

The model is defined in `app_files/arkplus_flat_models.py`, built by
`app_files/models.py`, and registered in `config.json["models"]` via its path key
(`arkplus_multihead`). The mapping from the model's NIH-14 head outputs to the
target DECAF lesions lives in `app_files/data_utils.py` (`MAPPING_REGISTRY`).

## Run the tutorial

```bash
make download-raw-checkpoint   # once: fetch the raw Ark6 checkpoint into models/
make run                       # prepares the checkpoint (if needed), then runs eval
```

Useful targets:

- `make prepare-checkpoint`: convert the raw checkpoint to a clean `.pt` only
- `make shell`: interactive shell in the simulator container
- `make down`: stop the simulator service
- `make clean`: remove generated simulator artifacts

## Key evaluation files

- `app_files/evaluator.py`: evaluation loop, per-model inference, per-lesion AUROC
- `app_files/arkplus_flat_models.py`: the `ArkSwinTransformer` model definition
- `app_files/models.py`: model factory (`model_paths` dict)
- `app_files/metrics_utils.py`: AUROC and head→lesion label mapping
- `app_files/data_utils.py`: data loading, DICOM parsing, label mappings, transforms
- `app_files/config.json`: model/checkpoint mapping and evaluation settings

## Evaluation output

Each client returns a DXO of kind `METRICS` whose data dict is saved by the
server into `evaluation_results.json` under the client's site name. The baseline
evaluator returns per-lesion AUROC for the single model.

### Example `evaluation_results.json`

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
        ...
    }
}
```

(Values above are illustrative, from a sample local run.)

### Fields

| Key | Type | Description |
|-----|------|-------------|
| `auroc_<Lesion>` | `float` | Area under the ROC curve for this lesion. Ranges `[0, 1]`; `NaN` if only one class is present in the ground truth. |

### Per-model output files

In addition to `evaluation_results.json`, the evaluator writes to
`manual_save_eval_results/` in the job workspace:

- `arkplus_pretrained_predictions.csv` — per-sample predictions and ground-truth labels
- `per_model_metrics.csv` — flat table of model, label, AUROC
- `run_metadata.json` — sample count, model list, label order

## Model code & `timm` compatibility

`app_files/arkplus_flat_models.py` adapts the `ArkSwinTransformer` from the
original Ark+ repository ([jlianglab/Ark](https://github.com/jlianglab/Ark)),
which pins **`timm==0.5.4`**. The model's `forward` is intentionally kept
identical to the upstream version:

```python
x = self.forward_features(x)
if self.projector:
    x = self.projector(x)
return x, self.omni_heads[head_n](x)
```

There is a subtle cross-version gotcha here. In **timm 0.5.4**,
`SwinTransformer.forward_features` pooled internally — it ended with
`AdaptiveAvgPool1d(1)` and returned a per-image `(B, C)` vector — so the Ark
`forward` never needed to pool. In **modern timm (1.x)**, that global average pool
was **moved out** of `forward_features` (it now lives in `forward_head`), and
`forward_features` returns the *unpooled* spatial feature map `(B, H, W, C)`
(a 24×24 grid for a 768px Swin).

This tutorial runs on modern timm, and the upstream `forward` bypasses
`forward_head` (it uses its own `omni_heads`). Without an explicit pool, the
heads therefore emitted a **per-location** grid of outputs instead of one
prediction per image — which produced mis-shaped predictions and made AUROC fail
with `ValueError: multi_class must be in ('ovo', 'ovr')`.

**Fix.** `forward` (and `generate_embeddings`) now apply an explicit
global-average-pool over the spatial dims right after `forward_features`,
restoring the timm 0.5.4 behaviour and matching the Swin head's default
`global_pool='avg'`:

```python
x = self.forward_features(x)
x = self._global_pool(x)   # mean over spatial dims; no-op if already (B, C)
```

**Verified equivalent.** Holding the backbone fixed, the explicit pool was
compared against an exact replica of timm 0.5.4's `AdaptiveAvgPool1d(1)` pooling.
The pooled features and all head outputs were **bit-for-bit identical**
(`max |Δ| = 0.0`) — averaging over the flattened sequence of tokens (`L`) is the
same operation as averaging over the `H×W` grid (`L = H×W`). The fix is also
shape-robust: it is a no-op if a future `timm` returns an already-pooled
`(B, C)` tensor.
