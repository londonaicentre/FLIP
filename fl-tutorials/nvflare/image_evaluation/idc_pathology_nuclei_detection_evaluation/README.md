# Federated nuclei detection on DICOM digital pathology

Evaluate one fixed nuclei detector across two institutions' whole-slide images, where **no pixel and
no annotation ever leaves either site** — only counts do.

## What you will learn

- How a FLIP *evaluation* job differs from a training job: no gradients, no weight aggregation, one
  broadcast model scored independently at every site.
- Why federated metrics must be pooled from confusion-matrix counts rather than averaged from
  per-site F1.
- Why a between-site difference means nothing until you know the within-site spread.

## Why federated evaluation?

Digital pathology slides are gigabytes each and rarely leave the institution that produced them. A
model developer who wants to know how their detector behaves across several hospitals therefore
cannot simply collect the data. FLIP sends the model instead and collects only aggregate statistics.

## Dataset: IDC digital pathology

[NCI Imaging Data Commons](https://portal.imaging.datacommons.cancer.gov/) publishes TCGA pathology
**already in DICOM Slide Microscopy** form, alongside the `pan_cancer_nuclei_seg_dicom` analysis
result, which supplies one POLYGON annotation per nucleus as a DICOM Microscopy Bulk Simple
Annotation (Supplement 222).

Nothing is re-hosted: `make -C fl-tutorials download-idc-pathology-data` pulls from IDC's public
buckets with no credentials, using `idc-index`. Roughly 2.1 GB for the default subset.

### How sites are formed

TCGA `PatientID` is the TCGA barcode, `TCGA-<TSS>-<participant>`, whose second field is the
**Tissue Source Site**. Sites are formed by grouping on it, so the partition is real rather than a
random split.

**TSS is a proxy, and a weak one.** It records where the *tissue* came from, not necessarily where
the slide was scanned, and TCGA slides were frequently digitised centrally. Read the cross-site
comparison as a demonstration of the machinery, not as a measurement of scanner or staining
heterogeneity.

One slide per patient is selected, so no patient contributes twice and none spans both sites.

## The reference annotations are not ground truth

`AnnotationGroupGenerationType` on these objects is `AUTOMATIC`: they are another model's output.
They are called *reference* detections throughout, never ground truth, and a disagreement is not
necessarily the detector being wrong.

## Model: a classical haematoxylin peak detector

The shipped detector deconvolves the H&E stain, flattens slowly varying background with a white
tophat, and takes local maxima of the haematoxylin channel above an adaptive per-tile threshold.

It needs **no dependency the FLIP FL runtime does not already carry**, which is why it is the
default: the tutorial runs on the published FL images with no rebuild. It is also, deliberately,
not a strong detector — see *Limitations*.

Parameters are **physical (micrometres)**, never pixels. The two default sites differ in
magnification (0.2325 vs 0.2470 um/px), so a pixel-valued parameter would mean different things at
each site and manufacture a "site effect" out of arithmetic.

### The model is the specification

A classical detector has no learned weights, but it does have a specification — and that
specification is what must be identical everywhere for the comparison to mean anything. So the
parameters *are* the model: `models.py` registers them as buffers, `make prepare-model` writes them
to `detector_specification.pt`, and `EvaluationModelLocator` broadcasts that to every site over the
`validate` task. Swapping in a learned detector changes what fills the `state_dict`, not how it
travels.

## Architecture

```text
                         FLIP server
                              |
              broadcast detector specification
                              |
              +---------------+---------------+
              |                               |
           site-1                          site-2
     (TSS A8 slides)                  (TSS A7 slides)
              |                               |
      tile -> detect -> match          tile -> detect -> match
              |                               |
        TP / FP / FN                    TP / FP / FN
              |                               |
              +---------------+---------------+
                              |
                    pooled confusion matrix
```

## Prerequisites

- The tutorial dataset (~2.1 GB), fetched below. No credentials, no GPU.
- Everything else comes from `flip-utils[full]`, which the Makefile uses directly.

## Run it

```bash
make -C fl-tutorials download-idc-pathology-data
make -C fl-tutorials run-tutorial TUTORIAL=idc_pathology_nuclei_detection_evaluation
```

Or from this directory: `make export` (wiring check, no data or GPU needed), `make sim` (the
simulation), `make summary` (recompute the table from the last run).

## Understanding the results

```text
Site       Slides  Tiles  Ref nuclei  Precision   Recall      F1  Patient F1 IQR
site-1          5    120        2319      0.793    0.569   0.662           0.041
site-2          5    120        1461      0.625    0.582   0.603           0.138
POOLED                          3780      0.718    0.574   0.638
```

**Pooled, not averaged.** Global precision/recall/F1 come from summed TP/FP/FN, so each site is
weighted by its evidence. The unweighted mean across sites is reported separately as
`macro_site_f1` and never called a global score — averaging would weight a site with 200 nuclei the
same as one with 20,000.

**The gap is not a result.** In the run above the between-site F1 gap is 0.059 while the widest
within-site patient IQR is 0.138 — more than twice as large. `summarise_results.py` prints a note
whenever that is the case, because a cross-site difference smaller than the within-site spread is
not evidence of a site effect. Tiles from one patient are not independent samples, so tile-level
intervals would be overconfident by roughly the square root of the tiles-per-patient count.

## What data leaves each hospital?

**Stays local:** slide pixels, annotation polygons, per-nucleus coordinates, per-tile counts,
per-patient rows.

**Returned:** `tp`, `fp`, `fn`, slide/tile/patient counts, derived rates, and a *summary* of the
per-patient F1 distribution (median, min, max, IQR) — never the per-patient values themselves.

A test pins the returned key set, because this is the tutorial's central claim.

## Limitations

- **The detector is weak.** It scores F1 ~0.64 pooled. Three independent classical variants (peak
  finding, white-tophat with an adaptive threshold, Laplacian-of-Gaussian blobs) all plateau near
  0.67, so this is the ceiling of the family rather than a tuning failure. Cellpose reaches ~0.83 on
  the same tiles but needs dependencies the FL runtime does not carry.
- **The reference is automatic**, so some disagreement is reference error.
- **Slides are size-capped** so the tutorial downloads ~2.1 GB rather than ~11 GB. That biases the
  subset toward smaller tissue sections, and toward the two sites that have such slides.
- **Defaults were tuned on one site-1 slide**, so site-1 has a slight home advantage. The
  improvement was checked on site-2 before being adopted.
- **This is a demonstration of federated evaluation infrastructure**, not a validated nuclei
  detector, and not a measurement of institutional variation.

## Running this on a real trust

You cannot yet, and the blocker is the platform rather than this tutorial. FLIP's imaging path
assumes radiology: `ResourceType` has no pathology member, imaging-api converts DICOM to NIfTI
(meaningless for a tiled RGB pyramid), XNAT's model is series-oriented, `trust/orthanc/` carries no
WSI plugin, and OMOP MI-CDM would need an `SM` modality concept. The dev stack's OMOP mock contains
only plain radiography, MR and CT.

The tutorial therefore runs under `LOCAL_DEV`, reading slides from disk — which is why it can be
developed and reviewed without any of that work landing first.

## Next steps

- **A learned detector.** Cellpose or HoVer-Net as a second entry in `config.json`'s `models`, which
  would also exercise the multi-model comparison the Ark+ evaluation tutorial already demonstrates.
- **Segmentation rather than detection**, scoring IoU/Dice against the polygons instead of reducing
  them to centroids.
- **Viewing the annotations in OHIF.** IDC publishes the same analysis result as DICOM-SEG as well
  as ANN, so no conversion is needed — but displaying it needs the pathology ingestion path above.
- **Pathology ingestion**, the platform work that would make all of the above deployable.
