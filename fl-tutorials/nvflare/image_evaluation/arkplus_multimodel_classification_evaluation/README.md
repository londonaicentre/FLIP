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

## Checkpoint setup

The evaluation app needs one checkpoint per model entry in `config.json["models"]`.
Checkpoints live in `app_files/` and are expected as `.pt` files.  `make run`
handles checkpoint preparation automatically (see below).

### Foundation model (arkplus_pretrained)

The zero-shot Ark+ checkpoint (`arkplus_pretrained_weights.pt`) requires
conversion from the raw Ark6 training output format into a clean state dict.
Obtain the raw checkpoint manually:

1. Download `Ark6_swinLarge768_ep50.pth.tar` from
   [Dropbox](https://www.dropbox.com/scl/fo/joycn8m93nvlrc8yjme40/ABBtPc5oaalYZ7kzmERpjhU/Ark%2B_Nature/Ark6_swinLarge768_ep50.pth.tar?rlkey=p3lphqvtgmiphw1n0u039jpjn&dl=1)
   (access via [this form](https://forms.gle/qkoDGXNiKRPTDdCe8)).
2. Run `make run RAW_CHECKPOINT=/path/to/Ark6_swinLarge768_ep50.pth.tar` — the
   Makefile's `check-raw-checkpoint` target runs `preprocess_checkpoints.py`
   automatically to produce the clean `.pt` file.

### Fine-tuned model (arkplus_finetuned)

The fine-tuned checkpoint is auto-downloaded from HuggingFace on `make run` if
not already present.  No manual steps required.

### Pre-processing internals

The conversion script lives at `process_tools/preprocess_checkpoints.py` — see
[process_tools/README.md](process_tools/README.md) for details on the
extraction and key-remapping logic.

## App configuration

Default local development settings are in `.env.app`:

- `JOB_TYPE=evaluation`
- `DEV_IMAGES_DIR=...`
- `DEV_DATAFRAME=...`

Model definitions live in `app_files/models.py` and are registered in
`config.json["models"]` via the path key (e.g. `arkplus_multihead`).

## Run the tutorial

```bash
# Auto-downloads the fine-tuned checkpoint from HuggingFace;
# fails if the foundation-model checkpoint is missing.
make run

# With a raw foundation-model checkpoint (pre-processes it automatically):
make run RAW_CHECKPOINT=/path/to/Ark6_swinLarge768_ep50.pth.tar

# Both checkpoints in one go:
make run RAW_CHECKPOINT=/path/to/Ark6_swinLarge768_ep50.pth.tar \
         FINETUNED_CHECKPOINT=/path/to/custom_finetuned.pt
```

Useful targets:

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

## Notes and troubleshooting

- DeLong p-values below machine epsilon are reported as `0.0`. The test is
  two-sided (H₀: AUC_a = AUC_b).
- If `delong_results.csv` is missing, confirm at least 2 models are configured
  in `config.json["models"]`.
