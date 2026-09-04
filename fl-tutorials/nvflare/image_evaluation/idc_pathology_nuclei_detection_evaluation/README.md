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

### Reproducibility without republishing the imaging

Slides are fetched from IDC on demand and never copied into another dataset. What *is* saved is the
much smaller thing needed to make a run repeatable:

- **`datasets/idc_pathology/manifest.csv`** — the lockfile. One row per slide, pinning its UIDs, its
  site and the IDC index version the selection was resolved against. IDC issues versioned releases
  and series come and go, so without this pin "the same criteria" silently resolves to a different
  subset later. The download target reproduces from it **by default**; `IDC_RESOLVE=1` re-selects
  and rewrites it, which should be reviewed as a deliberate dataset change.
- **`datasets/idc_pathology/omop/pathology_project/*.csv`** — the OMOP mock rows, derived from the
  manifest by `build_omop_project.py`. Deterministic: the same manifest regenerates them
  byte-identically.

Together these are a few kilobytes, so the repository carries the *selection* and its *description*
while the gigabytes stay at their source.

No demographics are invented. TCGA pathology DICOM is de-identified — every `PatientSex` and
`PatientBirthDate` in this collection is empty — so those columns carry OMOP's "No matching concept"
(0), and `year_of_birth`, which is `NOT NULL`, gets the deliberately implausible sentinel 1900 rather
than a realistic-looking year an analysis might believe.

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

## Seeing the nuclei

```bash
make overlays                              # 12 tiles of the default slide, plus an mp4
make overlays ACCESSION=TCGA-A7-A0DC TILES=20
```

Renders the tiles the federated run actually scored — same seed, same tissue threshold — with the
reference nuclei drawn as their true polygon outlines and every detection marked:

- **green** outline with a centre dot — reference matched by a detection (TP)
- **orange dashed** outline — reference the detector missed (FN)
- **magenta ×** — detection with no reference (FP)

The picture is more informative than the score. The misses are overwhelmingly *elongated,
faintly-stained* nuclei — stromal, fibroblast, vessel wall — while the matches are round, densely
stained epithelial nuclei. That is a specific, explainable limitation of a peak detector on a
haematoxylin channel, not uniform noise, and it is the sort of thing an F1 of 0.64 conceals.

This runs **locally on one site's data** and is deliberately not part of the federated job: rendering
per-nucleus overlays centrally would mean shipping pixels and per-nucleus coordinates off the site,
which is the exact thing the tutorial exists to avoid.

### Viewing the slides themselves

The slides are public and already served by IDC's DICOM-native slide viewer, so no local viewer is
needed to look at one — for example the first Trust_1 slide, `TCGA-A8-A0AB`:

<https://viewer.imaging.datacommons.cancer.gov/slim/studies/2.25.305523966109504368018351018821035186810/series/1.3.6.1.4.1.5962.99.1.1343238082.2143638158.1637725810626.2.0>

A link for any slide in the subset is `.../slim/studies/<slide_study_uid>/series/<slide_series_uid>`,
both of which are columns in `manifest.csv`.

Viewing them in **FLIP's own** stack is a different matter: XNAT here has no OHIF viewer at all (it is
deliberately not installed, FLIP#662), and OHIF's slide support is a separate microscopy extension
rather than its radiology core. So a FLIP-hosted slide view needs the ingestion work above *and* a
viewer that does not currently exist in the stack.

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

Not yet — but the gap is narrower than it first appears, and most of it is data rather than code.

What is **not** in the way, despite looking like it might be:

- `ResourceType` needs no new member. A whole-slide image *is* DICOM, and the evaluator already asks
  for `ResourceType.DICOM`.
- The DICOM-to-NIfTI conversion does not block anything. It is an XNAT *event subscription* that
  reacts to a scan being created and launches dcm2niix asynchronously; it would fail on a slide, but
  the DICOM resource is returned regardless.
- OMOP needs no new concept. The loaded DICOM vocabulary already carries Slide microscopy as
  `concept_id 2128009266`, which is what `image_occurrence.csv` here uses.
- Orthanc needs no WSI plugin to *store* slides — that plugin is for viewing.

What genuinely is in the way:

- **The dev OMOP mock has no slide-microscopy rows.** It carries plain radiography, MR and CT only.
  The generated `omop/pathology_project/` CSVs above are exactly what would fill that gap, and they
  already carry the `source_trust` column the seed pipeline partitions on.
- **A loader for a running trust.** FLIP#1101 adds `make -C trust seed`, which loads OMOP rows and
  the matching DICOM into a running trust by `source_trust`. Until it lands there is no in-tree way
  to add a project without rebuilding snapshots.
- **XNAT ingesting a slide.** This is the real unknown and worth proving before anything else: a
  slide is one multi-frame instance of 140 MB to 3 GB, and XNAT's importer is built around
  radiology series. Size matters too — the current Orthanc snapshot is about 1.1 GB in total.

The tutorial runs under `LOCAL_DEV`, reading slides from disk, which is why it can be developed and
reviewed without any of that landing first.

## Next steps

- **A learned detector.** Cellpose or HoVer-Net as a second entry in `config.json`'s `models`, which
  would also exercise the multi-model comparison the Ark+ evaluation tutorial already demonstrates.
- **Segmentation rather than detection**, scoring IoU/Dice against the polygons instead of reducing
  them to centroids.
- **Viewing the annotations in OHIF.** IDC publishes the same analysis result as DICOM-SEG as well
  as ANN, so no conversion is needed — but displaying it needs the pathology ingestion path above.
- **Pathology ingestion**, the platform work that would make all of the above deployable.
