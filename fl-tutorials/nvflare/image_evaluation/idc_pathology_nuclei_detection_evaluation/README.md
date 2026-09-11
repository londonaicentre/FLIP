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
buckets with no credentials, using `idc-index`. Roughly 6.4 GB for the default subset: 24 slides, 12 per
site, plus their annotation series.

### Reproducibility without republishing the imaging

Slides are fetched from IDC on demand and never copied into another dataset. What *is* published is
the much smaller thing needed to make a run repeatable, and it lives where every other project's
tables do — on [`aicentreflip/trust-data`](https://huggingface.co/datasets/aicentreflip/trust-data)
under `omop-csv/pathology_project/`, at the data version `trust/.data_version` pins (first published
as tag `20260911`). Nothing is committed to this repository:

- **`omop-csv/pathology_project/source/manifest.csv`** — the lockfile. One row per slide, pinning
  its UIDs, its site and the IDC index version the selection was resolved against. IDC issues
  versioned releases and series come and go, so without this pin "the same criteria" silently
  resolves to a different subset later. It sits beside the tables exactly as the cxr project's
  metadata table does (`omop-csv/cxr_project/source/dicom_metadata.csv`): the fixed input its OMOP
  export was built from. `make -C fl-tutorials fetch-idc-pathology-manifest` fetches it into the
  gitignored `fl-tutorials/data/idc_pathology/`, and the download target does that **by default**;
  `IDC_RESOLVE=1` re-selects instead and writes a fresh manifest there, which goes out as a **new data
  version** (`make -C trust publish-trust-data`, then bump the pin) — that publish is the review
  point for a dataset change, in place of a repository diff.
- **`omop-csv/pathology_project/*.csv`** — the four OMOP tables (`person`, `visit_occurrence`,
  `procedure_occurrence`, `image_occurrence`), derived from the manifest by
  `datasets/idc_pathology/build_omop_project.py`. Deterministic: the same manifest regenerates them
  byte-identically, which is what the shared gate certifies —
  `make -C fl-tutorials reproduce-idc-pathology-omop` fetches the manifest, rebuilds the tables into
  the gitignored `fl-tutorials/omop/<trust>/pathology_project/` and diffs them against the published
  export (`GATE PASS` at `20260911`). No `image_feature` is published: the labels are the nuclei
  annotations, which live in XNAT (data enrichment), not in OMOP.

Together these are about 20 KB, so the dataset carries the *selection* and its *description* while
the gigabytes stay at their source — there is deliberately no `dicom/pathology_project.tar.gz`.

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

- The tutorial dataset (~6.4 GB), fetched below. No credentials, no GPU.
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
site-1         12    288        5145      0.770    0.588   0.667           0.045
site-2         12    281        3224      0.607    0.587   0.597           0.123
POOLED                          8369      0.698    0.588   0.638
```

**Pooled, not averaged.** Global precision/recall/F1 come from summed TP/FP/FN, so each site is
weighted by its evidence. The unweighted mean across sites is reported separately as
`macro_site_f1` and never called a global score — averaging would weight a site with 200 nuclei the
same as one with 20,000.

**The gap is not a result.** In the run above the between-site F1 gap is 0.070 while the widest
within-site patient IQR is 0.123 — nearly twice as large. `summarise_results.py` prints a note
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
both of which are columns in the published `manifest.csv`.

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
- **Slides are size-capped** (400 MB per instance) so the tutorial downloads ~6.4 GB rather than the
  tens of GB that base-resolution slides run to (median ~890 MB, up to 3 GB each). That biases the
  subset toward smaller tissue sections, and toward the two sites that have such slides.
- **Defaults were tuned on one site-1 slide**, so site-1 has a slight home advantage. The
  improvement was checked on site-2 before being adopted.
- **This is a demonstration of federated evaluation infrastructure**, not a validated nuclei
  detector, and not a measurement of institutional variation.

## Running this on a real trust

Yes, as far as the data path goes. Cohort query → imaging pull → XNAT archive → viewer → data
enrichment has been driven through the platform against two dev trusts, not only in the simulator;
the federated evaluation run itself is still to be confirmed on a trust. It takes **two** data steps
rather than one, and the second is not optional.

```bash
make -C fl-tutorials seed-idc-pathology                                  # OMOP rows + slides
make -C fl-tutorials upload-idc-pathology-annotations \                  # annotations, after the pull
    FLIP_PROJECT_ID=<uuid> XNAT_URLS="http://host-1 http://host-2"
```

`seed-idc-pathology` is two halves keyed on the same published manifest. The OMOP rows go through the
platform's own seed pipeline — `make -C trust seed-omop KIT=<CODE> PROJECTS=pathology_project` for
each kit in `IDC_KITS` (default `GSTT KCH`), which fetches the published tables at the pinned data
version and loads that kit's `source_trust` slice. The slides are then posted into each trust's
Orthanc from the local IDC download by `datasets/idc_pathology/seed_slides.py`, because the seed
pipeline's DICOM half (`seed-orthanc`) expects a `dicom/<project>.tar.gz` on the dataset and
re-hosting the slides is exactly what this tutorial avoids. `DRY_RUN=1` reports the slide half and
skips the OMOP half (which has no dry run).

**Why two steps: the annotations cannot travel with the slides.** XNAT's DICOM receiver runs on
dcm4che 2.0.29, whose UID table has no entry for the Microscopy Bulk Simple Annotations SOP class
(`1.2.840.10008.5.1.4.1.1.91.1`). It offers no presentation context for it, so the C-STORE is
refused:

```
Cannot C-Store an instance of SOPClassUID 1.2.840.10008.5.1.4.1.1.91.1,
the destination has not accepted any TransferSyntax for this SOPClassUID
```

Slide and annotation share one accession, so that refusal fails the **whole study's** C-MOVE: an
annotation left in Orthanc stops the *slide* arriving too, and the study wedges in `ISSUED` while
the import status reports `Processing` indefinitely (FLIP#662) — indistinguishable, from the
counters alone, from a slow whole-slide transfer. So `seed-idc-pathology` seeds slides only, and the
annotations are uploaded to XNAT over its REST API, which negotiates no presentation contexts and
therefore carries any SOP class.

Each annotation is written **into the slide scan's own `DICOM` resource**, which is why the app
needs no special case: `flip.get_by_accession_number(..., ResourceType.DICOM)` returns that
resource's contents and `data_utils` picks the two objects apart by SOP Class, not by filename. A
scan's resource ends up holding both:

```
2.25.220751785271985364724971555318215948627-1-1-tvk325.dcm   265 MB   <- slide, named by SOP Instance UID
annotation.dcm                                                  34 MB   <- uploaded by enrichment
```

This mirrors the spleen tutorial, whose segmentation masks cannot live in OMOP and reach XNAT the
same way. Enrichment must visit **every** trust — each XNAT holds only its own studies — which is
what the repeated `--xnat-url` / `XNAT_URLS` roster is for; a run that resolves no destination
anywhere exits non-zero rather than looking clean.

What turned out **not** to be in the way, despite looking like it might be:

- `ResourceType` needs no new member. A whole-slide image *is* DICOM, and the evaluator already asks
  for `ResourceType.DICOM`.
- The DICOM-to-NIfTI conversion does not block anything. It is an XNAT *event subscription* that
  reacts to a scan being created and launches dcm2niix asynchronously; it would fail on a slide, but
  the DICOM resource is returned regardless.
- OMOP needs no new concept. The loaded DICOM vocabulary already carries Slide microscopy as
  `concept_id 2128009266`, which is what `image_occurrence.csv` here uses.
- Orthanc needs no WSI plugin to *store* slides — that plugin is for viewing.
- **XNAT ingests whole slides fine.** This was the open unknown; it is settled. The whole-slide SOP
  class *is* in dcm4che 2's table, and a 12-slide cohort (245–345 MB each) archived at each of two
  trusts. Note the finalisation delay: direct-archive sessions sit in `RECEIVING` until
  `sessionArchiveTimeoutInterval` (default 600 s) elapses after the last instance, so sessions
  appear ~10 minutes after transfer completes rather than immediately.

Two operational notes worth having before the first run:

- The cohort must clear the trust's `COHORT_QUERY_THRESHOLD` (default 10) before any row-level data
  is released, which is why the tutorial ships twelve slides per site rather than five.
- An Orthanc seeded by an older version of this tutorial still holds the annotation series;
  `seed-idc-pathology` prunes them, because leaving them behind reintroduces the C-MOVE failure
  above.

The tutorial also runs under `LOCAL_DEV`, reading slides from disk, so it can still be developed and
reviewed without a trust.

## Next steps

- **A learned detector.** Cellpose or HoVer-Net as a second entry in `config.json`'s `models`, which
  would also exercise the multi-model comparison the Ark+ evaluation tutorial already demonstrates.
- **Segmentation rather than detection**, scoring IoU/Dice against the polygons instead of reducing
  them to centroids.
- **Viewing the annotations in OHIF.** IDC publishes the same analysis result as DICOM-SEG as well
  as ANN, so no conversion is needed — but displaying it needs the pathology ingestion path above.
- **Pathology ingestion**, the platform work that would make all of the above deployable.
