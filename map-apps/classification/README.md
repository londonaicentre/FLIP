<!--
Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at
    http://www.apache.org/licenses/LICENSE-2.0
-->

# Classification MAP template (DICOM SR output)

Packages a FLIP classification model as a MAP that reads a DICOM study and writes a **DICOM
Structured Report**.

```
DICOMDataLoader → DICOMSeriesSelector → DICOMSeriesToVolume
                → FlipXrayClassifierOperator → DICOMTextSRWriterOperator → DICOM SR
```

## What you must change for your model

`classifier_operator.py` is deliberately explicit rather than configuration-driven, because every
one of the following is a place where a wrong assumption fails **silently** rather than loudly.

| Item | Where | Why it matters |
| --- | --- | --- |
| `LABELS` | `classifier_operator.py` | Channel order must match the training labels. Reversed labels produce a confident, wrong report. |
| Activation | `post_process` in `compute()` | Must match the training loss. The FLIP xray tutorial uses BCE, so outputs are **independent sigmoid** labels — a softmax would turn two findings into one either/or choice. |
| Preprocessing | `pre` in `compute()` | Must mirror the app's validation transforms, not its training transforms (no random augmentation). |
| Input size | `Resize(spatial_size=…)` | Must match what the network was trained on. |
| `POSITIVE_THRESHOLD` | `classifier_operator.py` | Reporting threshold; state it in the report text. |
| `Sample_Rules_Text` | `app.py` | Modality must match your data. Radiographs are `CR` or `DX`, **not** `CT` — a copied CT rule selects nothing and the app still exits 0. |
| `ModelInfo` / `EquipmentInfo` | `app.py` | Provenance recorded in the SR instance. |

## If you are targeting deepcOS

A deepcOS engine report expresses results as findings — a coded clinical concept with a confidence
score and a present or absent state — which is what `compute()` already produces internally before
formatting it as text. This template **now emits that report** alongside the DICOM SR, as
`*deepcReport.json`.

The logic lives in `deepc_report.py` (SDK-free, unit-tested under `tests/`); the operator calls it
after emitting the SR text. Two things it does deliberately:

- **Assigns a SNOMED CT concept to each label.** `LABEL_CODINGS` maps the two NIH labels this model
  emits — `Effusion` → *Pleural effusion* (`60046008`), `Edema` → *Pulmonary edema* (`19242006`).
  A label with no standard term is still reported, under a `LOCAL` scheme, using the vendor's
  documented escape hatch rather than being dropped.
- **Reports every label with an explicit present/absent state**, not only those above threshold —
  that is what lets a downstream system accept or reject each finding individually.

**What is yours to finish:** replace `LABEL_CODINGS` with the concepts for *your* model's labels,
and note that the report *shape* is a scaffold matching the fields the packaging guide records as
required (`allNegative` left null for a research model), **not** deepc's confidential schema —
validate against the vendor's own tooling before submission.

See [Deploying to deepcOS](../../docs/source/working-with-flip-apps/package-model-as-map.rst) for
the report's required fields and how it is discovered.

## Verifying

DICOM SR has no pixel data, so there is nothing to overlay — read the content back:

```python
import pydicom

ds = pydicom.dcmread("output/<instance>.dcm")
assert ds.Modality == "SR"
for item in ds.ContentSequence:
    if item.ValueType == "TEXT":
        print(item.TextValue)
```

**Keep report text ASCII.** The SR writer does not set `SpecificCharacterSet`, so values default to
the ISO-IR 6 repertoire and a character such as an em dash is stored as `?`. This is easy to miss:
the application log shows the correct string and only the stored instance is affected.
