# Spleen segmentation - FLIP tutorial

FLIP tutorial for training a 3D spleen segmentation model using CT scans from the [Medical Segmentation Decathlon (MSD)](http://medicaldecathlon.com/).

The training code is adapted from the MONAI spleen example:
<https://github.com/Project-MONAI/tutorials/blob/main/3d_segmentation/spleen_segmentation_3d.ipynb>

For a more advanced setup (Client API + FedAvg recipe), see:
<https://github.com/NVIDIA/NVFlare/tree/main/examples/advanced/monai/spleen_ct_segmentation>

## Compatible job type

This tutorial is designed for `JOB_TYPE=standard`.

## Prerequisites

- Python 3.12+
- Docker + Docker Compose
- Project dependencies installed from repo root (`uv sync`)
- `tutorials/testing/.env.testing` configured (at minimum `FL_BASE_IMAGE_TAG`, `NUM_CLIENTS`)

## Dataset setup

From this tutorial folder, create the local `uv` environment:

```bash
uv sync
```

Then run the dataset downloader:

```bash
uv run python utils/download_spleen_dataset.py \
  --output_dir ../../data/spleen/images \
  --num_cases 10
```

This downloads MSD spleen data and reorganizes it so each subject contains both image and label files.
Use `--num_cases` to control sample size (default `10`, maximum `41`).

Create the accession CSV used by the trainer:

```bash
uv run python utils/create_spleen_accession_csv.py \
  --images_dir ../../data/spleen/images \
  --output_csv ../../data/spleen/dataframe.csv
```

Expected structure:

```text
tutorials/data/spleen/
├── images/
│   ├── subject_1/
│   │   └── scans/
│   │       ├── input_spleen_1.nii.gz
│   │       └── label_spleen_1.nii.gz
│   ├── subject_2/
│   │   └── scans/
│   │       ├── input_spleen_2.nii.gz
│   │       └── label_spleen_2.nii.gz
│   └── ...
└── dataframe.csv
```

Use a CSV with an `accession_id` column (see example in [test dataset spleen/sample_get_dataframe_response.csv](https://huggingface.co/datasets/aicentreflip/flip-fl-base-test-data/blob/main/flip-fl-base-test-data/spleen/sample_get_dataframe_response.csv)).

## App configuration

Default local development settings are in `.env.app`:

- `JOB_TYPE=standard`
- `DEV_IMAGES_DIR=../../data/spleen/images`
- `DEV_DATAFRAME=../../data/spleen/dataframe.csv`

Update `DEV_DATAFRAME` to your CSV path, and ensure accession IDs match subject folder names (for example `subject_2`).

Training hyperparameters are in `app_files/config.json`:

- `LOCAL_ROUNDS`
- `GLOBAL_ROUNDS`
- `LEARNING_RATE`
- `VAL_SPLIT`

## Run the tutorial

From this folder:

```bash
make run
```

Useful targets:

- `make shell`: open an interactive shell in the simulator container
- `make down`: stop the simulator service
- `make clean`: remove generated local simulator artifacts

## Running on a real FLIP project: data enrichment

Everything above runs the tutorial against **local** data in the simulator. On a real FLIP project the
images come from each Trust's PACS — and PACS supply images only. A segmentation mask is a 3D volume
with nowhere to live in OMOP, so the labels have to be uploaded into each Trust's XNAT before training.
That upload is the platform's **data enrichment** stage.

(Contrast the chest X-ray classification tutorial, whose labels *are* in OMOP: its `query.sql` projects
them as dataframe columns and it needs no enrichment. See the Data Enrichment user guide.)

Each label must land in the **same scan's `NIFTI` resource** as its image, named to match — the training
code pairs them by filename, substituting `/input_` with `/label_`:

```text
NIFTI resource of one scan
├── input_spleen_2.nii.gz   # pulled from PACS, converted by FLIP
└── label_spleen_2.nii.gz   # uploaded by you
```

Run the upload **after the image pull and after DICOM-to-NIfTI conversion** — the target filename is
derived from the converted image, so running earlier silently skips every scan.

### Uploading the MSD labels

First download all 41 cases (the default of 10 covers only part of the cohort):

```bash
make -C fl-tutorials download-spleen-data NUM_CASES=41
```

Then, with network access to the Trust's XNAT and credentials in the environment:

```bash
export XNAT_HOST=https://xnat.trust.example
export XNAT_USER=your-username
export XNAT_PASS=your-password

make -C fl-tutorials upload-spleen-labels FLIP_PROJECT_ID=<project-uuid> TRUST=1 DRY_RUN=1
```

`DRY_RUN=1` reports what would happen without changing anything — do that first. Drop it to upload,
then repeat with `TRUST=2` and the second Trust's credentials.

The accession-to-case mapping is fetched at run time from the public `aicentreflip/trust-data` dataset
(the same mock data the Trusts are seeded from), so nothing needs to be checked in. `TRUST=` selects
which site's studies to upload using that dataset's `source_trust` column; omit it and the other
Trust's accessions are simply reported as "no matching scan".

Options: `OVERWRITE=1` to replace labels already in place, `XNAT_CREDENTIALS_FILE=<path>` to read
credentials from a JSON file instead of the environment.

This step is **backend-agnostic** — the labels live in XNAT, so a project enriched once can be trained
by either the NVFLARE or the Flower spleen tutorial. `fl-tutorials/flower` delegates to this same script.

## Notes and troubleshooting

- If you see `FL_BASE_IMAGE_TAG not set`, update `tutorials/testing/.env.testing`.
- If no training samples are found, training now fails with an explicit message reporting how many
  images were found against how many image/label pairs. Check:
  - CSV has `accession_id`
  - each accession maps to `<subject>/scans/input_*.nii.gz` and corresponding `label_*.nii.gz`
  - on a real FLIP project, that the data-enrichment upload above has run, **after** conversion
- The dataset downloader refuses to write into an existing output folder. Delete or rename the target directory before re-running.
