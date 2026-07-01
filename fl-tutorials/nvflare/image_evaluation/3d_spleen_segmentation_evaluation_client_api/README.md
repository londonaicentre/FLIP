# Evaluation of a 3D segmentation model (NVFLARE Client API) — FLIP tutorial

FLIP tutorial for federated evaluation of a 3D spleen segmentation model using the **NVFLARE Client
API**. This is the Client-API counterpart of
[`../3d_spleen_segmentation_evaluation`](../3d_spleen_segmentation_evaluation) (which uses the legacy
`RUN_EVALUATOR` executor); both evaluate a model trained with
[`../../image_segmentation/3d_spleen_segmentation`](../../image_segmentation/3d_spleen_segmentation).

## How it differs from the legacy evaluation tutorial

| | Legacy (`evaluation`) | This tutorial (`evaluation_client_api`) |
|---|---|---|
| Client code | `evaluator.py` is a `class FLIP_EVALUATOR(Executor)` | `evaluator.py` is a plain `nvflare.client` script (`flare.init/receive/send`) |
| Server flow | bespoke `ModelEval` + `EvaluationPTModelLocator` (multi-model `COLLECTION`) | shared `CrossSiteModelEval` validate path + single-model `EvaluationModelLocator` |
| Job definition | job-type template + harness | a Python `FlipEvalRecipe` driven by `job.py` |

The recipe loads the uploaded checkpoint on the server and broadcasts it to every client as a single
`FLModel`; the client's `is_evaluate()` branch scores it on the local cohort and returns aggregate
metrics. The `evaluation_results.json` output is unchanged.

## Compatible job type

This tutorial is designed for `JOB_TYPE=evaluation_client_api`.

## Prerequisites

- Python 3.12+, plus (for the SimEnv run) a GPU and the `flare-fl-base` image.
- The reference spleen dataset (shared with the segmentation tutorial — see
  [../../image_segmentation/3d_spleen_segmentation/README.md#dataset-setup](../../image_segmentation/3d_spleen_segmentation/README.md#dataset-setup)).

## Checkpoint setup

The evaluation app needs a model checkpoint in `app_files/`. From this folder:

```bash
make download-checkpoints
```

The checkpoint URL is configured in `.env.app` as `MODEL_CHECKPOINT_URL`.

## App configuration

Default local development settings are in `.env.app`:

- `JOB_TYPE=evaluation_client_api`
- `DEV_IMAGES_DIR=../../data/spleen/images`
- `DEV_DATAFRAME=../../data/spleen/dataframe.csv`
- `MODEL_CHECKPOINT_URL=https://huggingface.co/aicentreflip/tutorials-evaluation-3d-seg-model/resolve/main/model.pt`

The `DEV_*` paths point at the shared, gitignored `fl-tutorials/nvflare/data/spleen` dataset produced by
`make -C fl-tutorials download-spleen-data`; `make sim` resolves them to absolute paths so the
simulator's client workers find the data. Evaluation settings (e.g. `num_classes`, the `models`
checkpoint mapping) are in `app_files/config.json`.

`make sim`/`make export`/`make run` run `job.py` in the **flip-utils** environment with the `full` ML
extra — the same package set the `flare-fl-base` FL image installs (`uv sync --extra full`) — so a local
run matches the deployed image.

## Run the tutorial

`job.py` drives the recipe in two modes.

```bash
# Export the complete NVFLARE job for review or Docker deployment (no GPU needed)
make export        # writes ./fl_job/flip_evaluation/

# SimEnv local simulation (requires GPU + data + checkpoint)
make run           # downloads the checkpoint, then runs the simulator via `make sim`
```

Useful targets: `make download-checkpoints`, `make clean` (removes `./fl_job`).

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

Per-sample (row-level) scores are deliberately not exported: a per-patient list would leak the exact
evaluation cohort size and be linkable to individual patients.
