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

# Chest-X-ray multi-lesion classification (Flower)

Flower port of the chest-X-ray tutorial that lives at
`flip-fl-base/tutorials/image_classification/xray_classification`. It scores
every X-ray against the lesions named in `app/config.json` (Effusion + Edema
by default, with "Lungs in normal arrangement" as a negative override) using
a MONAI DenseNet121 trained with multi-label BCE.

The cohort query (`query.sql`) is copied verbatim from the NVFLARE tutorial
and matches the chest-X-ray data seeded into the trust mock OMOP DB
(concept ids 4215818 / 4196943 / 40481136). This is the tutorial that
`make e2e_smoke` from FLIP picks when `FL_BACKEND=flower`.

## Folder structure

```
xray_classification/
├── query.sql                  # Cohort SQL — verbatim from flip-fl-base
├── pyproject.toml             # Dependency manifest; not consumed by FLIP (base bundle wins)
├── README.md
└── app/
    ├── __init__.py
    ├── config.json            # Per-tutorial hyperparameters (LOCAL_ROUNDS, splits, LESIONS, ...)
    ├── client_app.py          # @app.train + @app.evaluate
    ├── task.py                # train_func / validate_func helpers
    ├── data_loading.py        # FLIP_BASE + lesion / row helpers
    ├── transforms.py          # MONAI X-ray transforms
    ├── models.py              # DenseNet121
    └── loss_and_metrics.py    # BCE loss + per-lesion P/R/F1
```

`server_app.py` and `strategy.py` are intentionally absent. FLIP's base bundle
provides the canonical `app/server_app.py` and overrides any user upload at
bundle time (`bundle_flower_application` skips reserved names with a warning),
so shipping a per-tutorial `server_app.py` would only be useful for `flwr run`
simulation — and the spleen tutorial's README already documents why `flwr run`
is brittle here. Better to not invite the footgun.

## Running through FLIP (recommended)

From the FLIP repo root:

```bash
make up FL_BACKEND=flower    # bring up the Flower compose stack
make e2e_smoke               # picks this tutorial automatically because FL_BACKEND=flower
```

`make e2e_smoke` reads its `MODEL_FILES_DIR` and `QUERY_FILE` from the
`FL_BACKEND` value (see `flip-api/Makefile`) and points them at this
tutorial. Override either to swap in a different cohort:

```bash
make e2e_smoke QUERY_FILE=/abs/path/to/your_cohort.sql
```

## What goes through the upload

`bundle_flower_application` in flip-api copies every file in `app/` into
`app/<filename>` under the destination bundle, then overlays the base
bundle's `app/server_app.py` and root-level `pyproject.toml`. That means:

| File                      | Wins after bundling? |
|---------------------------|----------------------|
| `app/client_app.py`       | uploaded (required)  |
| `app/models.py`           | uploaded (required)  |
| `app/task.py`             | uploaded             |
| `app/data_loading.py`     | uploaded             |
| `app/transforms.py`       | uploaded             |
| `app/loss_and_metrics.py` | uploaded             |
| `app/__init__.py`         | uploaded             |
| `app/config.json`         | uploaded — read by client_app.py |
| `app/server_app.py`       | not shipped — base wins for any user-uploaded copy |
| `pyproject.toml`          | not uploaded by the smoke (sits at tutorial root); base bundle's wins regardless |

So `app/config.json` is the only knob worth changing per tutorial — the base
`pyproject.toml` is shared across every Flower tutorial.

## Hyperparameters

`app/config.json` mirrors the NVFLARE chest-X-ray config:

| Key                  | Default | Meaning |
|----------------------|---------|---------|
| `LOCAL_ROUNDS`       | 3       | Local epochs per global round |
| `LR_START` / `LR_END`| 1e-3 / 1e-4 | ExponentialLR sweep across the local round |
| `VAL_SPLIT`          | 0.2     | Validation fraction |
| `TEST_SPLIT`         | 0.2     | Test fraction (used by `@app.evaluate`) |
| `BATCH_SIZE`         | 8       | DataLoader batch size |
| `LESIONS`            | Effusion / Edema / "Lungs in normal arrangement" | Multi-label heads + the normal-override column |
| `value_to_numerical` | {0:"No",1:"Yes"} | Maps dataframe string values to binary labels |
| `VALIDATE_EVERY`     | 1       | Validate every N epochs (currently always 1) |

## Data assumptions

- `query.sql` resolves chest-X-ray accession IDs labeled with effusion / edema
  / normal lungs from the trust mock OMOP DB.
- DICOM payloads come back via `flip.get_by_accession_number(..., resource_type=[ResourceType.DICOM])`.
- Each `.dcm` becomes one training sample with the row's lesion labels.
