# Evaluation of a 3D segmentation model - FLIP tutorial

FLIP tutorial for federated evaluation of a 3D spleen segmentation model.

This app is intended to evaluate a model trained with:
`tutorials/image_segmentation/3d_spleen_segmentation`.

## Compatible job type

This tutorial is designed for `JOB_TYPE=evaluation`.

## Prerequisites

- Python 3.12+
- Docker + Docker Compose
- `tutorials/testing/.env.testing` configured (at minimum `FL_BASE_IMAGE_TAG`, `NUM_CLIENTS`)

## Dataset setup

This app expects the same spleen dataset layout used by the segmentation tutorial.

If you have not prepared it yet, from the segmentation tutorial folder run:

```bash
cd tutorials/image_segmentation/3d_spleen_segmentation
uv sync
uv run python utils/download_spleen_dataset.py \
  --output_dir ../../data/spleen/images \
  --num_cases 10
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
│   └── ...
└── dataframe.csv
```

## Checkpoint setup

The evaluation app needs a model checkpoint in `app_files/`.

From this folder:

```bash
cd tutorials/image_evaluation/3d_spleen_segmentation_evaluation
make download-checkpoints
```

By default, the checkpoint URL is configured in `.env.app` as `MODEL_CHECKPOINT_URL`.

## App configuration

Default local development settings are in `.env.app`:

- `JOB_TYPE=evaluation`
- `DEV_IMAGES_DIR=../data/spleen/images`
- `DEV_DATAFRAME=../data/spleen/dataframe.csv`
- `MODEL_CHECKPOINT_URL=https://huggingface.co/aicentreflip/tutorials-evaluation-3d-seg-model/resolve/main/model.pt`

Update these paths if your local data/checkpoint locations differ.

Evaluation settings are defined in `app_files/config.json`.

## Run the tutorial

From this folder:

```bash
cd tutorials/image_evaluation/3d_spleen_segmentation_evaluation
make run
```

Useful targets:

- `make shell`: open an interactive shell in the simulator container
- `make down`: stop the simulator service
- `make clean`: remove generated local simulator artifacts

## Key evaluation files

- `app_files/evaluator.py`: evaluation loop and metric computation
- `app_files/models.py`: model definitions and checkpoint loading
- `app_files/transforms.py`: inference/evaluation transforms
- `app_files/config.json`: model/checkpoint mapping and evaluation output schema

## Notes and troubleshooting

- If checkpoint download fails, confirm `MODEL_CHECKPOINT_URL` is reachable.
- If evaluation finds no samples, verify:
  - CSV contains `accession_id`
  - each accession has `scans/input_*.nii.gz` and matching `label_*.nii.gz`
- If you see `FL_BASE_IMAGE_TAG not set`, update `tutorials/testing/.env.testing`.
