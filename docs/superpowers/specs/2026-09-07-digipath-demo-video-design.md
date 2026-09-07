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

# Recording a digital-pathology demo video

**Status:** design, awaiting review
**Date:** 2026-09-07
**Goal:** `make demo-video DEMO_ARGS="--app digipath"` produces `flip-demo-digipath.mp4`

## What this is for

The demo-video recorder can film two tutorials, chest X-ray and 3D spleen segmentation. This adds a
third: the IDC digital-pathology nuclei-detection evaluation. The output is a video; everything
below exists only to make that video show the real platform rather than a staged approximation.

That framing decides several trade-offs later in this document. Where a choice is between "more
faithful" and "more general", this takes the faithful one only when it is what the camera sees.

## Why it is more than an `APPS` entry

The recorder drives the platform's actual lifecycle: create project, submit a cohort query, wait for
imaging to be pulled from the trust PACS into XNAT, upload an app, run it, download results. The two
existing apps work because their data is already in that path.

The pathology tutorial has never been in it. It runs under `LOCAL_DEV`, where `flip.get_dataframe`
validates the cohort query and then ignores it, reading a per-site `dataframe.csv` from disk. Today:

| | state |
| --- | --- |
| Slide-microscopy series in either trust's Orthanc | none (of 4,263 studies) |
| Pathology rows in either trust's OMOP | none loaded; the CSVs are committed in-tree |
| Slides in XNAT | five, imported by hand while debugging the OHIF viewer |

So `--app digipath` against today's stack would create a project, submit a cohort matching nothing,
and wait for an image pull that never starts.

### A correction to the tutorial's own note

`query.sql` currently claims a real-trust run needs a pathology route that "does not exist yet",
listing four blockers. Three were already retracted earlier in this work, and the fourth has since
fallen over:

| Claimed blocker | Actual state |
| --- | --- |
| `ResourceType` has no pathology member | `ResourceType.DICOM` exists and covers whole-slide imaging |
| imaging-api converts DICOM to NIfTI | That is an async event subscription, not in the pull's critical path |
| XNAT's model is series-oriented | XNAT archives `xnat:smSessionData` and serves it over DICOMweb (verified) |
| OMOP MI-CDM needs an `SM` modality concept | Concept `2128009266` already exists and the tutorial's OMOP builder uses it |

The comment overstates the difficulty and must be corrected as part of this work. What remains is
plumbing — moving data into paths that already exist — not platform redesign.

## Non-goals

- **Nuclei overlaid inside OHIF.** Not possible with the deployed viewer: the bundled
  `dicom-microscopy-viewer` exposes `addAnnotationGroups()`, but the XNAT plugin's app layer never
  calls it, and its ROI machinery serves the cornerstone radiology viewport instead. The video shows
  the slide in OHIF and the nuclei from `process_tools/render_overlays.py`.
- **Publishing whole-slide imaging to Hugging Face.** Slides are downloaded from IDC; only the small
  OMOP CSVs are committed, which is the existing reproducibility decision for this tutorial.
- **Kubernetes.** The recorder targets the local dev stack.
- **Changing the tutorial's science.** Detector, metrics and thresholds are untouched.

## Architecture

Five pieces, ordered by risk rather than by dependency, because the first one can invalidate the
rest.

```
  IDC download (already on disk)
  fl-tutorials/data/idc_pathology/{Trust_1,Trust_2}/accession-resources/<accession>/slide.dcm
            │
            │  (1) seed
            ▼
      Orthanc (per trust)  ──────┐
                                 │  (2) cohort query matches accession_id
  OMOP CSVs (committed in-tree)  │      imaging-api pulls study → XNAT
  .../omop/pathology_project/*.csv
            │  (1) seed          │
            ▼                    ▼
      OMOP db (per trust) ──► XNAT session (xnat:smSessionData)
                                 │
                                 │  (3) OHIF reads it over DICOMweb   ← segment 3 films this
                                 ▼
                        FL evaluation job  ← segments 4-6 film this
```

### 0. The dataset is below the trust disclosure threshold (found by the gate; blocks everything)

The gate never reached the pull. Staging failed first:

```
Trusts [...] returned no cohort records (zero or privacy-suppressed) and cannot be staged
```

This is the platform working correctly. `COHORT_QUERY_THRESHOLD` defaults to **10** — each trust's
own disclosure floor, enforced trust-side on every row-level cohort route — and the tutorial pins
`IDC_SLIDES_PER_SITE = 5`. Five is below ten, so each trust refuses to release its cohort, and the
refusal is deliberately indistinguishable from an empty one.

`LOCAL_DEV` never exercised this because it bypasses the cohort entirely, reading `dataframe.csv`
off disk. The constraint has always been there; nothing had ever asked.

**The only acceptable fix is more slides.** Lowering the threshold would weaken a privacy control to
make a demo work, on the very tutorial whose selling point is that slides and per-nucleus
coordinates never leave the trust. It is not on the table.

Raising `IDC_SLIDES_PER_SITE` to at least 10 is a **deliberate dataset change**: it re-resolves the
committed manifest, roughly doubles the download (~1.8 GB to ~3.6 GB), and shifts every metric the
tutorial's README reports, since the evaluation would then run over more slides. That is the
tutorial author's call, not an implementation detail, so it is escalated rather than assumed.

### 1. Validate the pull (blocked on the above)

Whether imaging-api can carry a slide-microscopy study from Orthanc into XNAT is unknown and
untested. Everything else is wasted if it cannot.

This is deliberately the first task, and it is cheap: seed one slide into one trust's Orthanc, add
its OMOP rows, submit the tutorial's cohort query through a real project, and watch whether an
`xnat:smSessionData` session appears. Known specifics that make this plausible rather than a
gamble: `ResourceType.DICOM` exists, XNAT archives the type (proven this week), and the
DICOM-to-NIfTI subscription is asynchronous and non-fatal.

**Re-scope trigger.** If the pull cannot carry an SM study, stop and return to the choice between a
"from XNAT onwards" recording and fixing imaging-api. Do not build around it silently.

### 1b. Two defects in the never-run federated path (found while seeding; both fixed)

The tutorial's federated branch was written but had never executed, so neither showed up until the
data was in a trust:

- **The app located its two DICOM objects by filename** (`slide.dcm`, `annotation.dcm`). That is what
  the tutorial's download produces and not what a trust returns — imaging-api hands back whatever
  XNAT stored, named by SOP Instance UID. The first real run would have failed with
  `FileNotFoundError` for files that were present. Both are now found by SOP Class.
- **The seeder posted only the slide.** The evaluation scores against the reference annotations, so
  that trust would have pulled, decoded, and then had nothing to compare against. Both objects share
  an accession and a study, so one pull carries both — but only if both are in Orthanc.

### 1c. imaging-api rejected every study (found by the pull; fixed)

With the cohort staged, the pull failed all twelve accessions at once. Orthanc answered correctly —
accession, patient, `modalitiesInStudy: ["ANN","SM"]` — and imaging-api rejected the response:

```
2 validation errors for Study
  referringPhysicianName  Field required
  patient.sex             Field required
```

`PatientSex` (0010,0040) and `ReferringPhysicianName` (0008,0090) are DICOM **Type 2**: present in an
instance but permitted to be zero-length, and DQR omits an empty value from its JSON altogether. The
schema was stricter than the standard it models.

**Nothing about this is pathology-specific.** Any de-identified cohort would have hit it; it had gone
unnoticed only because the dev PACS snapshot happens to populate both fields. Fixed by defaulting
both to `""`.

Worth recording for the next person: the operator-visible symptom was `QueueFailed=12` and an
aborted pull, with the real cause only in imaging-api's log. `QueueFailed` for *every* accession
normally means an empty PACS — here the PACS was answering perfectly.

### 2. Seeding

Neither `make update-orthanc-data` nor `make update-omop-data` can be reused: both are wholesale
replacements that download a prepared tarball and swap the data directory. Seeding here must be
**additive against a running stack**.

The precedent is `fl-tutorials/datasets/spleen/upload_spleen_labels_to_xnat.py` — an in-tree script
that talks to running trust services over HTTP. This follows it:

- **`fl-tutorials/datasets/idc_pathology/seed_orthanc.py`** — `POST /instances` each `slide.dcm` to
  each trust's Orthanc. Idempotent: Orthanc returns the existing instance id for a re-POST, so a
  re-run is a no-op rather than a duplicate.
- **`fl-tutorials/datasets/idc_pathology/seed_omop.py`** — insert the four committed CSVs into each
  trust's OMOP, filtered by `source_trust`. Idempotent on the primary keys the tutorial's
  `build_omop_project.py` already assigns from `ID_BASE = 4_000_000` (rows begin at `4000002`).

**The OMOP `source_trust` column is authoritative for which trust gets what**, not the
`Trust_1`/`Trust_2` directory names on disk. They agree today — five accessions each, `TCGA-A8-*`
to trust 1 and `TCGA-A7-*` to trust 2 — because the directory layout was generated from the same
column. Reading the column rather than the path keeps one source of truth and makes the agreement
testable instead of assumed.

Both are driven by one target, `make -C fl-tutorials seed-idc-pathology`, documented as a
prerequisite of the recording exactly as `download-spleen-data` is for the spleen smoke.

**Why a prerequisite rather than a recorder hook.** The recorder's `--data-enrichment-cmd` runs
*after* the image pull. OMOP rows have to exist *before* the cohort query in segment 1, so the
existing hook is the wrong place and a new pre-flight hook would earn nothing over a make target.

### 3. Recorder support

- `APPS["digipath"]` — on-camera project and model names, one backend (`nvflare`); the tutorial is
  an evaluation recipe and has no Flower counterpart.
- **Evaluation needs no recorder support.** This design originally assumed it would, since segments
  4-6 are written around "create model + train". It does not: `e2e_smoke_spleen_evaluation` is
  nothing but `MODEL_FILES_DIR` / `QUERY_FILE` overrides, and model creation carries no job type.
  Pointing the profile at the evaluation app directory is the whole difference.
- The uploaded artefact is small: the digipath "checkpoint" is a generated specification file
  carrying the detector's physical parameters, not model weights, so nothing here strains
  `MAX_MODEL_FILE_BYTES` or the upload segment's timing.

### 4. Segment 3 (XNAT/OHIF)

The existing segment opens XNAT and shows a session in OHIF. For digipath it shows a whole-slide
image, which the viewer renders in its "Slide" mode with a different layout — an `SM` thumbnail
rather than an image stack. Expect selector work in the Cypress segment; expect no backend work,
since this path is already verified end to end.

`siteUrl` must be browser-reachable or the viewport renders black. That is fixed and tested, but it
is a hard prerequisite of this segment and is called out here because the failure is silent.

### 5. Cleanup

Correct the stale blocker comment in `query.sql`, and the corresponding README section it points at.

## Error handling

The failure this design most wants to avoid is the silent one — a recording that completes and
quietly shows the wrong thing.

- **Seeding fails closed.** A seed run that places no slides, or no OMOP rows, in *any* trust exits
  non-zero, following the `--allow-no-op` precedent in the spleen uploader. A wholly-skipped seed
  must not let the recorder walk into a doomed run.
- **The pull already fails loudly**: the cohort's accession list is what drives it, so an empty
  cohort surfaces at the image-pull wait rather than during training.
- **Per-trust, not global.** Each trust's Orthanc and OMOP get only their own `source_trust` rows,
  so a partial seed shows up as a site with no data rather than as a plausible-looking half-cohort.

## Testing

Following the repo's rule that a test touching a real backing service is an integration test, and
that tutorial-tree tests live in `fl-tutorials/tests/` and run CPU-only:

- **Unit (`fl-tutorials/tests/`)** — the `source_trust` partitioning of both seed scripts against
  synthesised CSVs: every row goes to exactly one trust, and the accessions seeded into Orthanc are
  the accessions written into that trust's OMOP. This is the invariant that makes a federated
  demo honest, and it is cheap to check without either service.
- **Idempotency** — re-running a seed against the same fixture produces no duplicate rows.
- **Static** — `APPS["digipath"]` names a tutorial directory and query file that exist, mirroring
  the guard the existing apps have.
- **Manual, once** — the pull validation in step 1, and one full recording.

No test asserts that the video looks right; that is what watching it is for.

## Open risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| imaging-api cannot carry an SM study | Design collapses to "from XNAT onwards" | Step 1, before anything else is built |
| Orthanc rejects or mishandles a 140 MB tiled instance | Seeding needs a different route | Surfaces in step 1; five instances per trust, so a per-instance failure is visible immediately |
| Recorder's evaluation support is a larger change than it looks | Slips the video | Follow `e2e_smoke`'s existing evaluation path rather than inventing one |
| OHIF segment selectors differ for the slide layout | Segment 3 needs rework | Cosmetic; the backend path is already verified |

### Two things that look like risks and are not

Both cost time to chase during design; recorded so nobody chases them again.

- **XNAT strips `AccessionNumber` on import.** The anon script deletes `(0008,0050)` outright. The
  pull does not care: imaging-api passes `accession_id` explicitly as the XNAT experiment label
  (`upload.py`, `experiment_id_or_label=accession_id`), so the association never depended on the tag
  surviving anonymisation.
- **The OMOP `image_series_uid` will not match XNAT's.** The anon script hashes study, series and
  SOP UIDs on import, so the archived slide's UIDs differ from the source UIDs recorded in the
  committed CSV. Nothing in this flow matches on them — the cohort query selects `accession_id` and
  the pull is driven by that — so it is a cosmetic inconsistency in the mock data, not a defect.
  Worth knowing before someone "fixes" it.

## Sequencing

1. Validate the pull (**gate** — report back before continuing)
2. Seed scripts + make target + their unit tests
3. Recorder `APPS` entry + evaluation support
4. Segment 3 selectors for the slide layout
5. Record, watch, iterate
6. Correct `query.sql` and the README

Steps 2-4 are independent once step 1 passes.
