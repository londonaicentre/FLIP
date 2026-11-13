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

# Ark+ chest X-ray dataset

Chest X-ray data for the three Ark+ tutorials (NVIDIA FLARE only), downloaded from the Hugging Face dataset
`aicentreflip/tutorials-arkplus-cxr-classification`.

| Splits | Folders | Used by | Make target |
|--------|---------|---------|-------------|
| TRAIN | `site1`, `site2` | [`arkplus_fine_tuning`](../../nvflare/image_classification/arkplus_fine_tuning/README.md) | `download-arkplus-finetuning-data` |
| HOLD-OUT | `site1_holdoff`, `site2_holdoff` | [`arkplus_baseline_classification_evaluation`](../../nvflare/image_evaluation/arkplus_baseline_classification_evaluation/README.md), [`arkplus_multimodel_classification_evaluation`](../../nvflare/image_evaluation/arkplus_multimodel_classification_evaluation/README.md) | `download-arkplus-eval-data` |

## Download

Run from the repository root, through the fl-tutorials Makefile (see [`../README.md`](../README.md)):

```bash
make -C fl-tutorials download-arkplus-finetuning-data  # TRAIN splits, ~6.3 GB
make -C fl-tutorials download-arkplus-eval-data        # HOLD-OUT splits, ~1.6 GB
```

## How it works

Both Make targets call one script, `download_arkplus_dataset.py`, with different `--sites`. It fetches each site folder from Hugging Face and lays it out as:

```text
<site>/
├── accession-resources/
└── sample_get_dataframe_response.csv
```

Only the TRAIN download passes `--write-marker`. Before skipping a re-download, `make reproduce-overhead` in `arkplus_fine_tuning` checks for that marker, not just for the directory.

This directory has no uv project of its own. The script's only dependency is `huggingface_hub`, so the Make targets run it with `uv run --no-project --with huggingface_hub`.
