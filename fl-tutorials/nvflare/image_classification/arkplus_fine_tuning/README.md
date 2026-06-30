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

# Chest-X-ray multi-lesion classification with Ark+ (FLIP/NVFLARE)

This app follows the NVFLARE-style FLIP tutorial layout used by:

```text
flip-fl-base/tutorials/image_classification/xray_classification/app_files/
```

It keeps the original FLIP chest-X-ray classification workflow and replaces the
original DenseNet model with an Ark+ FedArk chest-Xray model wrapper. The trainer
also supports Ark+-style local teacher/student training inside each FLIP client,
while NVFLARE remains responsible for global/master aggregation.

The first goal is a default-data smoke test: all participating sites are assumed
to expose the same labels from the original xray tutorial:

```text
Effusion
Edema
Lungs in normal arrangement
```

`Effusion` and `Edema` are the model outputs. `Lungs in normal arrangement` is
used as a negative override: when it is positive, the lesion labels are treated
as negative.

## Folder structure

```text
xray_classification/
├── query.sql
├── README.md
├── ARKPLUS_NVFLARE_APPFILES_NOTES.md
└── app_files/
    ├── config.json
    ├── data_utils.py
    ├── loss_and_metrics.py
    ├── models.py
    ├── trainer.py
    ├── validator.py
    └── vendor/Ark-main/
```

## App flow

```text
query.sql
        ↓
returns accession_id plus Effusion / Edema / normal-lungs labels
        ↓
trainer.py / validator.py fetch DICOMs by accession_id
        ↓
data_utils.py loads each DICOM as [B, 1, 224, 224]
        ↓
trainer.py calls model(images)
        ↓
models.py ArkPlusNVFlareWrapper repeats channels to [B, 3, 224, 224]
        ↓
student Ark+ FedArk model runs with the configured head_id
        ↓
wrapper returns features plus logits [B, 2]
        ↓
trainer.py computes BCE label loss plus optional teacher/student consistency loss
        ↓
local teacher is updated by EMA from the student
        ↓
student weights are returned to NVFLARE for server-side aggregation
```

## Ark+ integration

`app_files/models.py` exposes the NVFLARE-required function:

```python
get_model()
```

That function loads Ark+'s FedArk chest-Xray model from:

```text
app_files/vendor/Ark-main/Ark_Plus/Distributed/FedArk_ChestXrays/models.py
```

Ark+ normally expects 3-channel input and returns both features and logits. The
local wrapper adapts this to the original FLIP trainer interface:

```text
FLIP expects: model(images) -> logits
Ark+ returns: model(images, head_id) -> features, logits
Wrapper gives: model(images) -> logits
```

This keeps `trainer.py`, `validator.py`, and the original BCE/metric logic close
to the original tutorial while using Ark+ as the network.

## Configuration

The main settings live in:

```text
app_files/config.json
```

The default label configuration is:

```json
"LESIONS": {
  "0": "Effusion",
  "1": "Edema",
  "-1": "Lungs in normal arrangement"
}
```

The Ark+ output configuration should match the two real model outputs:

```json
"ARKPLUS": {
  "USE_TEACHER_STUDENT": true,
  "NUM_CLASSES_LIST": [2],
  "HEAD_ID": 0,
  "EMA_MODE": "epoch",
  "TEACHER_MOMENTUM": 0.9,
  "CONSISTENCY_WEIGHT": 0.1
}
```

With teacher/student enabled, each FLIP client owns two local models:

```text
student: trainable model loaded from the NVFLARE global/master weights
teacher: frozen local EMA copy used as a consistency target
```

During each local training epoch, the student learns from both the xray labels
and the teacher feature representation. Only the student weights are sent back
to NVFLARE. The local teacher is not aggregated by the server.

## Data assumptions

- `query.sql` returns one row per image occurrence with an `accession_id`.
- The returned dataframe must include columns named `Effusion`, `Edema`, and
  `Lungs in normal arrangement`.
- DICOM images are fetched by accession number using FLIP:

```python
flip.get_by_accession_number(..., resource_type=[ResourceType.DICOM])
```

- Each `.dcm` file becomes one training, validation, or test sample.
- No per-image JSON label file is required. Labels come from the dataframe
  produced by the SQL query.


## Per-site data in the local NVFLARE simulator

The local simulator can run multiple simulated clients inside one container. To
let each simulated client read a different dataset, this app uses the NVFLARE
client name, for example `site-1` or `site-2`, to select a data entry from
`app_files/config.json`:

```json
"SITE_DATA": {
  "site-1": {
    "images_dir": "/site-data/site-1/accession-resources",
    "dataframe": "/site-data/site-1/sample_get_dataframe_response.csv"
  },
  "site-2": {
    "images_dir": "/site-data/site-2/accession-resources",
    "dataframe": "/site-data/site-2/sample_get_dataframe_response.csv"
  }
}
```

For the current smoke test, both `site-1` and `site-2` are mounted from the same
existing local dataset:

```text
.test_data/flip-fl-base-test-data/xrays_mini_300/accession-resources
.test_data/flip-fl-base-test-data/xrays_mini_300/sample_get_dataframe_response.csv
```

Those host paths are mounted into the container by the compose files used for
testing. To use truly different site datasets later, change the host-side paths
in the compose/environment setup so each site mount points to a different image
folder and dataframe CSV. The container-side paths in `SITE_DATA` can stay the
same unless you also change the mount targets.

Relevant files:

```text
app_files/config.json
tutorials/testing/compose.yml
deploy/compose.test.yml
```

If `SITE_DATA` is missing or the site name is unknown, the loader falls back to
the older single-dataset variables `DEV_IMAGES_DIR` and `DEV_DATAFRAME`.

## Dependency note

Ark+'s FedArk model imports `timm`. If the FLIP/NVFLARE runtime does not include
`timm`, model construction will fail. Add `timm` to the runtime dependencies, or
temporarily set:

```json
"REQUIRE_ARKPLUS_IMPORT": false
```

inside `app_files/config.json` only for a non-Ark fallback smoke test.
