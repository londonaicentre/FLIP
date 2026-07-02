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

# Ark+ Fine-tuning — Chest X-ray Classification (FLIP tutorial)

FLIP tutorial for **federated fine-tuning** of an Ark+ Swin foundation model on
chest X-ray classification. Each FLIP client trains a local Ark+ model on its own
hold-out data and NVFLARE aggregates the updates; an optional Ark+-style local
teacher/student (EMA) loop runs inside each client.

## Compatible job type

This tutorial is a **standard** FL training job (`config.json["job_type"] = "standard"`).

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

`Lungs in normal arrangement` is a negative override: when it is positive, all
lesion labels for that row are treated as negative. Labels come from the per-site
dataframe (see Dataset setup) — there is no per-image label file.

## Model & Ark+ integration

NVFLARE's `PTFileModelPersistor` loads `models.get_model` from
`app_files/models.py`. `get_model()`:

1. builds an `ArkSwinTransformer` (defined in `app_files/arkplus_flat_models.py`)
   via `arkplus_flat_models.build_omni_model`, sized from the `ARKPLUS` block in
   `config.json`;
2. initialises it from a local backbone checkpoint
   `app_files/pretrained_weights.pt` (see Checkpoint setup) — with
   `LOAD_BACKBONE_ONLY=true` only the backbone is loaded, the heads start fresh;
3. wraps it in `ArkPlusNVFlareWrapper`, which adapts Ark+'s
   `model(images, head_id) -> (features, logits)` to the `model(images) -> logits`
   interface the FLIP trainer/validator expect (and exposes `forward_with_features`
   for the teacher/student loop).

The `ARKPLUS` block configures the network:

```json
"ARKPLUS": {
  "MODEL_NAME": "swin_large_384",
  "INPUT_SIZE": 768,
  "PROJECTOR_FEATURES": 1376,
  "USE_MLP": false,
  "NUM_CLASSES_LIST": [5],
  "HEAD_ID": 0,
  "LOAD_BACKBONE_ONLY": true,
  "USE_TEACHER_STUDENT": true,
  "EMA_MODE": "epoch",
  "TEACHER_MOMENTUM": 0.9,
  "CONSISTENCY_WEIGHT": 0.1,
  "USE_AMP": true,
  "AMP_DTYPE": "float16"
}
```

### Teacher/student training

When `USE_TEACHER_STUDENT=true`, each client holds two local models:

- **student** — trainable, initialised from the NVFLARE global weights;
- **teacher** — a frozen EMA copy, updated from the student (`EMA_MODE`,
  `TEACHER_MOMENTUM`), used as a consistency target.

The per-step loss combines a BCE label loss (`loss_and_metrics.get_bce_loss`) with
an MSE feature-consistency loss between student and teacher embeddings
(`forward_with_features`), weighted by `CONSISTENCY_WEIGHT`. Only the **student**
weights are returned to NVFLARE; the teacher is never aggregated.

## Configuration

Training settings live in `app_files/config.json`, e.g. `GLOBAL_ROUNDS`,
`LOCAL_ROUNDS`, `LR_START`/`LR_END`, `VAL_SPLIT`/`SPLIT_SEED`,
`BATCH_SIZE`, plus the `LESIONS` and `ARKPLUS` blocks above.

## Dataset setup

This app expects a DECAF-formatted chest X-ray dataset:

- A per-site CSV dataframe with `accession_id` and the lesion-label columns above
- DICOM images organised under `<images_dir>/<accession_id>/...`

Data is selected per simulated client by NVFLARE client name (`site-1`, `site-2`)
from the `SITE_DATA` block in `config.json`:

```json
"SITE_DATA": {
  "site-1": { "images_dir": "/site-data/site-1/accession-resources",
              "dataframe":  "/site-data/site-1/sample_get_dataframe_response.csv" },
  "site-2": { "images_dir": "/site-data/site-2/accession-resources",
              "dataframe":  "/site-data/site-2/sample_get_dataframe_response.csv" }
}
```

If `SITE_DATA` is missing or the site name is unknown, the loader falls back to
`DEV_IMAGES_DIR` / `DEV_DATAFRAME` (settable in `.env.app`). Host paths are mounted
into the simulator container by the testing harness. Each DICOM is loaded, resized
to `ARKPLUS.INPUT_SIZE`, intensity-scaled, then repeated 1→3 channels and
ImageNet-normalised before inference (`app_files/data_utils.py`).

### Simulator vs. real deployment

`SITE_DATA` and the `/site-data/site-N/...` mounts are **simulator-only**. The NVFLARE
simulator runs every client (`site-1`, `site-2`) inside one container, so per-site data must
be given a distinct mount target selected by client name — this is local development only.

On a real federated client the fl-client runs with `LOCAL_DEV=false`, and the data layer
ignores `SITE_DATA` entirely: the cohort dataframe comes from
`FLIP().get_dataframe(project_id, query)` and DICOMs from `FLIP().get_by_accession_number(...)`
(the trust's data-access-api / imaging-api), reading downloaded images from the single shared
`/app/data/images` mount — there are no per-site mounts. `project_id`/`query` are supplied by
the FL job config (`config_fed_client.json` → `RUN_TRAINER`/`RUN_VALIDATOR`). The switch is
keyed on `LOCAL_DEV` in `app_files/data_utils.py` (`_is_local_dev`), mirroring the flip
package's own `FLIPStandardDev`/`FLIPStandardProd` selection.

The cohort query for a real deployment is [`query.sql`](query.sql) — it selects the chest X-ray
**training** set and returns the seven lesion-label columns the model expects. Pass it as the
project's cohort query (e.g. `make e2e_smoke QUERY_FILE=.../arkplus_fine_tuning/query.sql`).

## Checkpoint setup

`get_model()` requires the backbone checkpoint at
`app_files/pretrained_weights.pt`. It is produced from the raw Ark6 training
output (`Ark6_swinLarge768_ep50.pth.tar`) by `make run`:

```bash
make run RAW_CHECKPOINT=/path/to/Ark6_swinLarge768_ep50.pth.tar
```

The Makefile's `check-raw-checkpoint` target runs
`process_tools/preprocess_checkpoints.py` to convert the raw checkpoint into the
clean `pretrained_weights.pt` (a no-op if it already exists). Access to the raw
checkpoint is via [this form](https://forms.gle/qkoDGXNiKRPTDdCe8); see
[process_tools/README.md](process_tools/README.md) for the conversion details.

## Run the tutorial

```bash
make run RAW_CHECKPOINT=/path/to/Ark6_swinLarge768_ep50.pth.tar
```

Useful targets:

- `make shell`: interactive shell in the simulator container
- `make down`: stop the simulator service
- `make clean`: remove generated simulator artifacts

## Key files

- `app_files/models.py`: `get_model()` factory + `ArkPlusNVFlareWrapper`
- `app_files/arkplus_flat_models.py`: the `ArkSwinTransformer` definition and `build_omni_model`
- `app_files/arkplus_flat_utils.py`: Swin pretrained-key remapping helpers
- `app_files/trainer.py`: FL training loop, teacher/student EMA, losses
- `app_files/validator.py`: validation/metrics
- `app_files/loss_and_metrics.py`: BCE loss and precision/recall/F1
- `app_files/data_utils.py`: data loading, DICOM parsing, label mapping, transforms
- `app_files/config.json`: model, training, and per-site data settings

## Dependency note

The Ark+ model imports `timm`. If the FLIP/NVFLARE runtime does not include
`timm`, model construction fails — add it to the runtime dependencies, or set
`ARKPLUS.REQUIRE_ARKPLUS_IMPORT=false` in `config.json` only for a non-Ark
fallback smoke test.

## Model code & `timm` compatibility

`app_files/arkplus_flat_models.py` defines the `ArkSwinTransformer` (built by
`build_omni_model`, used via `models.get_model()`). It is adapted from the original
Ark+ repository ([jlianglab/Ark](https://github.com/jlianglab/Ark)), which pins
**`timm==0.5.4`**, and keeps the upstream `forward`
(`forward_features` → projector → `omni_heads`).

Cross-version gotcha: in **timm 0.5.4**, `SwinTransformer.forward_features` pooled
internally (`AdaptiveAvgPool1d(1)`) and returned a per-image `(B, C)` vector, so
the Ark `forward` never needed to pool. In **modern timm (1.x)** that average pool
was moved into `forward_head`, and `forward_features` now returns the *unpooled*
spatial map `(B, H, W, C)` (a 24×24 grid for a 768px Swin). Because the upstream
`forward` bypasses `forward_head` (it uses its own `omni_heads`), on modern timm
the heads produced a **per-location** grid of logits instead of one
`(B, num_classes)` prediction per image.

This app did not crash on that, because `ArkPlusNVFlareWrapper._pool_logits`
already averaged 4-D spatial logits back to `(B, num_classes)` — but that pooling
happened **after** the heads, and the teacher/student path
(`forward_with_features`) still received the unpooled spatial features.

**Fix.** `forward` (and `generate_embeddings`) now apply an explicit
global-average-pool over the spatial dims **right after `forward_features`** — the
architecturally-correct place — matching the Swin head's default
`global_pool='avg'`:

```python
x = self.forward_features(x)
x = self._global_pool(x)   # mean over spatial dims; no-op if already (B, C)
```

This restores the timm 0.5.4 behaviour, makes the teacher/student consistency loss
operate on pooled embeddings, and reduces `_pool_logits` to a harmless no-op (the
logits are already `(B, C)`). With a linear projector (`USE_MLP=false`) it is
numerically identical to the old wrapper-level logit pooling; unlike that
workaround, it is also correct when `USE_MLP=true` (a non-linear projector).

**Verified equivalent.** Holding the backbone fixed, the explicit pool is
bit-for-bit identical (`max |Δ| = 0.0`) to timm 0.5.4's `AdaptiveAvgPool1d(1)`
pooling — averaging over the flattened token sequence (`L`) equals averaging over
the `H×W` grid (`L = H×W`). It is also a no-op if a future `timm` returns an
already-pooled `(B, C)` tensor.
