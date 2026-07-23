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
| Orientation | `Flip` in `pre` | **Do not transcribe your training chain's orientation transform literally.** Training loads DICOM with MONAI's `LoadImaged`, which returns the array transposed — `(column, row)`, not `PixelData`'s `(row, column)` — so its orientation transform is calibrated to that transpose. `DICOMSeriesToVolumeOperator` does not transpose, so the equivalent step here is a different transform. For the xray tutorial, training's `Rotate90d(k=-1)` composes with the transpose into a plain left-right mirror, which is why this operator uses `Flip(spatial_axis=1)`. Derive yours by comparison, not by copying (see below). |
| Input size | `Resize(spatial_size=…)` | Must match what the network was trained on. |
| `POSITIVE_THRESHOLD` | `classifier_operator.py` | Reporting threshold; state it in the report text. |
| `Sample_Rules_Text` | `app.py` | Modality must match your data. Radiographs are `CR` or `DX`, **not** `CT` — a copied CT rule selects nothing and the app still exits 0. |
| `ModelInfo` / `EquipmentInfo` | `app.py` | Provenance recorded in the SR instance. |

## Checking the orientation for your own model

Run one study through both paths and compare the arrays — do not reason about it. Dump what
`DICOMSeriesToVolumeOperator` hands the operator, dump what your training chain's validation
transforms produce for the same file, then find which of the eight rotation/flip combinations maps
one onto the other:

```python
for k in range(4):
    for name, fn in (("", lambda x: x), ("+fliplr", np.fliplr), ("+flipud", np.flipud)):
        print(k, name, np.abs(fn(np.rot90(map_array, k)) - training_array).max())
```

The correct one gives `0.0` and every other is far from it, so the answer is unambiguous. Do this on
a **non-square** image if you can: on a square one, a transpose and a rotation are indistinguishable
by shape and it is easy to conclude the wrong thing. A mis-set orientation does not raise — it just
degrades predictions, and for a chest radiograph a mirror also silently swaps left and right.

## If you are targeting deepcOS

This template is closer to ready than the segmentation one. A deepcOS engine report expresses
results as findings — a coded clinical concept with a confidence score and a present or absent
state — which is what `compute()` already produces internally before formatting it as text. The
remaining work is assigning coded concepts to the label names and emitting the report alongside
the SR, not changing how inference works.

Report **every** label the model can produce with an explicit present/absent state, not only those
above threshold: that is what lets downstream systems accept or reject findings individually. This
template already evaluates every label, so it is a formatting change rather than a behavioural one.

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
