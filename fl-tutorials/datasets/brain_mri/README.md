# Brain MRI dataset (MSD Task01_BrainTumour)

The 3-D MRI cohort for the tutorials: **MSD Task01_BrainTumour** — the Medical Segmentation
Decathlon's cut of BraTS 2016/17 (Antonelli et al., *Nat Commun* 2022; CC BY-SA 4.0). 484 training
cases, each a 240×240×155×4 NIfTI whose four co-registered channels are FLAIR, T1w, T1Gd and T2w,
with a three-class tumour label. Nothing here belongs to a real person: the patient identities the
mock studies carry are synthetic and derived from the case id (`../utils/synthetic_identity.py`).

Three things are produced from it, all under the gitignored `fl-tutorials/data/`:

| Output | Path | Consumed by |
| --- | --- | --- |
| Raw MSD extract | `data/Task01_BrainTumour/{imagesTr,labelsTr,dataset.json}` | the two chains below |
| Simulator layout | `data/brain_mri/images/<accession>/scans/{input_<channel>_<case>,label_<case>}.nii.gz` + `dataframe.csv` | tutorials under `LOCAL_DEV` (the latent-diffusion tutorial, once retargeted) |
| Mock trust data | `data/brain_mri/dicom/<accession>/*.dcm` (four MR series per study) + `source/dicom_metadata.csv` + `omop/…` + `canonical/…` | a running dev trust's Orthanc and OMOP (`seed-brain-mri`), the published tables |

## What is published, and what is not

**Only the OMOP tables and the metadata table they were built from go to Hugging Face**
(`aicentreflip/trust-data`, `omop-csv/brain_mri_project/` — the `pathology_project` precedent). The
DICOM set is never re-hosted: the converter is deterministic, so it regenerates byte-for-byte from the
MSD tar on any machine — every UID and identity is a pure function of the MSD case id
(`../utils/dicom_writer.py`) — and `convert-brain-mri-to-dicom METADATA_TABLE=…` converts exactly the
cases the published table names. The published cohort is the first 40 training cases in natural order
(`BRAIN_MRI_NUM_CASES=40`), round-robin across two trusts (`source_trust` 1/2, 20 studies each — above
the trusts' 10-row `COHORT_QUERY_THRESHOLD`).

## Targets

All run from the repo root as `make -C fl-tutorials <target>`; every path below is relative to
`fl-tutorials/`.

```bash
make -C fl-tutorials download-brain-mri-msd-raw            # 7.6 GB tar (md5-checked) -> data/Task01_BrainTumour/ (training half)
make -C fl-tutorials download-brain-mri-data NUM_CASES=10  # simulator layout -> data/brain_mri/images + dataframe.csv

# The mock-trust chain (no root, no binaries):
make -C fl-tutorials convert-brain-mri-to-dicom            # -> data/brain_mri/dicom/<accession>/*.dcm  (BRAIN_MRI_NUM_CASES=40)
make -C fl-tutorials create-brain-mri-metadata-table       # -> data/brain_mri/source/dicom_metadata.csv (one row per series)
make -C fl-tutorials build-brain-mri-omop-tables           # -> omop/brain_mri_project + omop/trust_{1,2}/brain_mri_project
make -C fl-tutorials build-brain-mri-canonical             # -> data/brain_mri/canonical/brain_mri_project (source_trust form)
make -C fl-tutorials verify-brain-mri-dicom                # DICOM tree <-> canonical tables, both ways
make -C fl-tutorials seed-brain-mri KIT=GSTT               # both halves of a RUNNING dev trust, from the local tree

# The reproducible path (published metadata table -> tables -> gate):
make -C fl-tutorials reproduce-brain-mri-omop              # fetch + build + verify against the pinned data version
make -C fl-tutorials convert-brain-mri-to-dicom METADATA_TABLE=data/brain_mri/source/dicom_metadata.csv   # exactly the published cohort
```

`seed-brain-mri` loads the OMOP rows from `canonical/` (`make -C trust seed-omop … CANONICAL_DIR=`)
and POSTs the instances from `dicom/` (`make -C trust seed-orthanc … DICOM_SOURCE= TABLES_DIR=`), so a
platform pull can be proven before a data version is tagged. After publishing,
`make -C trust seed-omop KIT=<CODE> PROJECTS=brain_mri_project` loads the same rows from the tag; the
DICOM half always comes from the local tree. `DRY_RUN=1` counts the DICOM half and loads nothing.

Driving the platform end to end (cohort query → Orthanc → XNAT → dcm2niix), with `query.sql` here
as the cohort and any NVFLARE app as the model files:

```bash
make e2e_smoke MODEL_FILES_DIR=../fl-tutorials/nvflare/image_synthesis/latent_diffusion_model/app_files \
  QUERY_FILE=../fl-tutorials/datasets/brain_mri/query.sql EXTRA_ARGS="--trusts GSTT --abort-midway --image-pull-timeout 3600"
```

Each pulled session ends with four scans, each carrying one `input_<FLAIR|T1w|T1Gd|T2w>_…nii.gz` in its
`NIFTI` resource — `ProtocolName` is the channel label, which dcm2niix puts in the filename.

## The DICOM studies

One MR study per case: `StudyDescription "MR Brain WO and W contrast IV"` (LOINC 24587-8, the
procedure the OMOP export records), `BodyPartExamined BRAIN`, one `StudyInstanceUID` and
`FrameOfReferenceUID`, and four series numbered in channel order with `SeriesDescription` =
`ProtocolName` = `FLAIR` / `T1w` / `T1Gd` / `T2w` and plausible acquisition parameters (the T1Gd series
carries a `ContrastBolusAgent`). Pixels are stored as unsigned 16-bit; geometry follows the NIfTI
affine into LPS. The MSD case id rides in `ClinicalTrialSubjectID` (0012,0040), which is how the
metadata table's `Subject` column is recovered from the tree.

## The OMOP tables

`omop_convert_brain_mri.py` builds, on the shared contract in `../utils/`: `person` per case
(`person_id` = the first nine digits of the synthetic NHS number), `visit_occurrence` and
`procedure_occurrence` per study (concept 3037128), `image_occurrence` per **series** (four rows share
an `accession_id`; anatomic site 4133034 *Brain structure*, modality 4013636), and `image_feature` +
`measurement` for the five DICOM attributes spleen publishes (Manufacturer, ManufacturerModelName,
SliceThickness in mm, Rows, Columns). Surrogate keys come from `brain_mri_project`'s reserved block
(`5_000_000`; `../utils/omop_ids.py`). The tumour labels are deliberately **not** in OMOP: the
dataset's first use is unsupervised (latent diffusion), and a segmentation mask has nowhere to live in
a cohort query anyway — it would reach a project by XNAT data enrichment, as the spleen labels do.

## Tests

`make -C fl-tutorials pytest-datasets` runs `tests/datasets/brain_mri/` in this directory's own uv
environment (`pyproject.toml`): case selection, the archive filter and checksum gate, the converter
(four series, channel pixels, determinism), the metadata table and its quality gates, the OMOP
conversion and the simulator layout — all on a synthetic three-case extract, no download.
