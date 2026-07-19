<!--
Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at
    http://www.apache.org/licenses/LICENSE-2.0
-->

# Segmentation MAP template (DICOM SEG output)

Packages a FLIP segmentation model as a MAP that reads a DICOM study and writes a **DICOM
Segmentation** object referencing the source series.

```
DICOMDataLoader → DICOMSeriesSelector → DICOMSeriesToVolume
                → MonaiBundleInferenceOperator → DICOMSegmentationWriterOperator → DICOM SEG
                                               ↘ STLConversionOperator → surface mesh
```

Adapted from `ai_spleen_seg_app` in the MONAI Deploy App SDK examples (Apache 2.0, MONAI
Consortium copyright retained).

## Why this template needs almost no editing

Inference is bundle-driven: `MonaiBundleInferenceOperator` reads the preprocessing, inferer and
postprocessing from the `inference.json` embedded in your `model.ts`. Retargeting to a different
segmentation model therefore means changing the **bundle**, not the operator graph.

Only two blocks in `app.py` are application-specific:

| Block | What to change |
| --- | --- |
| `segment_descriptions` | One `SegmentDescription` per segment: the label, the SNOMED category and type codes, and the algorithm name/version. These end up in the SEG instance and are what a viewer displays. |
| `Sample_Rules_Text` | The series-selection rule. The default selects CT; change the modality and any description pattern to match the series your model expects. |

## The bundle is where correctness lives

The `inference.json` embedded in `model.ts` must declare the **same preprocessing the model was
trained with** — orientation, spacing, intensity window, channel handling — and the inferer ROI
must match the patch size used in training.

This is the highest-risk step in the whole path. A mismatch does not raise: the MAP runs, emits a
well-formed DICOM SEG, and segments slightly (or entirely) wrongly. For a worked example of how
easily this happens, the FLIP spleen tutorial windows CT at `a_max=250` while the MONAI Model Zoo
spleen bundle — same architecture — uses `a_max=164`.

See [`fl-tutorials/nvflare/image_segmentation/3d_spleen_segmentation/export/`](../../fl-tutorials/nvflare/image_segmentation/3d_spleen_segmentation/export)
for a worked pair of bundle configs, and the
[packaging guide](../../docs/source/working-with-flip-apps/package-model-as-map.rst) for how to
transcribe transforms from a training application.

## Verifying

Do not trust the exit code. Confirm the artefact is a real, correctly-referenced SEG:

```python
import pydicom

ds = pydicom.dcmread("output/<instance>.dcm")
assert ds.Modality == "SEG"
assert ds.SOPClassUID == "1.2.840.10008.5.1.4.1.1.66.4"
print(ds.SegmentSequence[0].SegmentLabel)
print(ds.ReferencedSeriesSequence[0].SeriesInstanceUID)   # must be the input series
print(ds.FrameOfReferenceUID)                             # must match the source
```

Then view it overlaid on the source study — a standalone Orthanc with the OHIF plugin is
sufficient, and is the quickest way to catch an inverted or misaligned mask.
