# Evaluating a spleen segmentation model

FLIP tutorial for federated evaluation of a 3D spleen segmentation model using the **FLARE Client
API**. It evaluates a model trained with
[`../../image_segmentation/3d_spleen_segmentation`](../../image_segmentation/3d_spleen_segmentation).

The recipe loads the uploaded checkpoint on the server and broadcasts it to every client as a single
`FLModel`; the client's `is_evaluate()` branch scores it on the local cohort and returns aggregate
metrics, which are collected into `evaluation_results.json`.

## At a glance

| Item | Value |
|---|---|
| What you upload | everything in `app_files/`, plus `model.pt` from `make download-checkpoints` |
| Job type | evaluation |
| Imaging | on, DICOM to NIfTI on; labels uploaded as data enrichment |
| Cohort query | the training tutorial's [`query.sql`](../../image_segmentation/3d_spleen_segmentation/query.sql) |
| Dataset | `make -C fl-tutorials download-spleen-data` |
| Simulator | `make -C fl-tutorials run-tutorial TUTORIAL=3d_spleen_segmentation_evaluation` (GPU) |

## Prerequisites

- Python 3.12+.
- A GPU, for the local simulator run (`make run`). Exporting the job with `make export` needs no GPU.
- The reference spleen dataset, shared with the segmentation tutorial: run `make -C fl-tutorials download-spleen-data` (see the segmentation tutorial's [dataset setup](../../image_segmentation/3d_spleen_segmentation/README.md#dataset-setup-local-runs)).

## Checkpoint setup

The evaluation app needs a model checkpoint in `app_files/`. From this folder:

```bash
make download-checkpoints
```

The checkpoint is downloaded from `MODEL_CHECKPOINT_URL` in `.env.app` (see [App configuration](#app-configuration)).
`make run` and `make export` run this step for you.

## App configuration

Default local development settings are in `.env.app`:

- `JOB_TYPE=evaluation`
- `DEV_IMAGES_DIR=../../../data/spleen/images`
- `DEV_DATAFRAME=../../../data/spleen/dataframe.csv`
- `MODEL_CHECKPOINT_URL=https://huggingface.co/aicentreflip/tutorials-evaluation-3d-seg-model/resolve/main/model.pt`

The `DEV_*` paths point at the shared, gitignored `fl-tutorials/data/spleen` dataset produced by
`make -C fl-tutorials download-spleen-data`; `make sim` resolves them to absolute paths so the
simulator's client workers find the data. Evaluation settings (e.g. `num_classes`, the `models`
checkpoint mapping) are in `app_files/config.json`.

`make sim`/`make export`/`make run` run `job.py` in the **flip-utils** environment with the `full` ML
extra — the same package set the `flare-fl-base` FL image installs (`uv sync --extra full`) — so a local
run matches the deployed image.

## Run the tutorial

`job.py` builds the recipe and either exports the job or runs it in the FLARE simulator. From this folder:

```bash
# Export the complete FLARE job for review or Docker deployment (no GPU needed)
make export        # writes ./fl_job/flip_evaluation/

# SimEnv local simulation (requires GPU + data + checkpoint)
make run           # downloads the checkpoint, then runs the simulator via `make sim`
```

To remove the exported job: `make clean` (deletes `./fl_job`).

## Key files

- `app_files/evaluator.py`: the Client-API evaluation loop (receive model → score → send metrics).
- `app_files/models.py`: model definition + `get_model()`.
- `app_files/transforms.py`: inference/evaluation transforms.
- `app_files/config.json`: `num_classes`, `net_config`, and the `models` checkpoint mapping.
- `job.py`: builds `FlipEvalRecipe` and runs export / SimEnv.

## Output metrics

The evaluator returns **aggregate** (cohort-mean) metrics only, collected into `evaluation_results.json`:

- `mean_dice` — mean Dice coefficient
- `mean_hausdorff_95` — 95th-percentile Hausdorff distance (voxels)
- `mean_surface_dice` — normalized Surface Dice at a 1-voxel tolerance (`SURFACE_DICE_TOLERANCE_VOXELS`)
- `mean_iou` — mean intersection-over-union

The FL server also sends them to the hub, where they show on the model page per trust (`MEAN_DICE`, …).

Per-sample (row-level) scores are deliberately not exported: a per-patient list would leak the exact
evaluation cohort size and be linkable to individual patients.

## Migrating from the legacy Executor-API evaluation app

This tutorial replaces the retired Executor-API evaluation tutorial. If you are porting an old app, the differences are:

| | Legacy Executor flow | This tutorial (`evaluation`) |
|---|---|---|
| Client code | `evaluator.py` is a `class FLIP_EVALUATOR(Executor)` | `evaluator.py` is a plain `nvflare.client` script (`flare.init/receive/send`) |
| Server flow | bespoke `ModelEval` + `EvaluationPTModelLocator` (multi-model `COLLECTION`) | shared `CrossSiteModelEval` validate path + `EvaluationModelLocator` (one single-model validate task per entry in `config['models']`) |
| Job definition | job-type template + harness | a Python `FlipEvalRecipe` driven by `job.py` |

The `evaluation_results.json` output format is the same as the legacy flow's.
