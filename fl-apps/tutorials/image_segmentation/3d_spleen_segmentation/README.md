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
cd tutorials/image_segmentation/3d_spleen_segmentation
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

Use a CSV with an `accession_id` column (example: `.test_data/spleen/sample_get_dataframe_response.csv`).

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
cd tutorials/image_segmentation/3d_spleen_segmentation
make run
```

Useful targets:

- `make shell`: open an interactive shell in the simulator container
- `make down`: stop the simulator service
- `make clean`: remove generated local simulator artifacts

## Notes and troubleshooting

- If you see `FL_BASE_IMAGE_TAG not set`, update `tutorials/testing/.env.testing`.
- If no training samples are found, check:
  - CSV has `accession_id`
  - each accession maps to `<subject>/scans/input_*.nii.gz` and corresponding `label_*.nii.gz`
- The dataset downloader refuses to write into an existing output folder. Delete or rename the target directory before re-running.
