# 3D Spleen Segmentation — FLIP tutorial

This tutorial trains a 3D UNet spleen segmentation model on CT scans from the
[Medical Segmentation Decathlon (MSD)](http://medicaldecathlon.com/) using the **NVFLARE Client API**
(`nvflare.client`). The job is defined entirely in Python via `FlipFedAvgRecipe` — no hand-written
JSON configs required.

It replaced the retired Executor-API tutorial of the same name (removed along with the other
legacy-syntax NVFLARE tutorials); unlike that legacy flow there is no separate `validator.py` —
validation runs server-side via global model evaluation.

The training code is adapted from the MONAI spleen example:
<https://github.com/Project-MONAI/tutorials/blob/main/3d_segmentation/spleen_segmentation_3d.ipynb>

## Compatible job type

This tutorial is designed for `JOB_TYPE=standard`.

## Data requirements

Each converted `input_*.nii.gz` CT volume must have a sibling `label_*.nii.gz` segmentation mask in
the same folder. The trainer QC-skips non-3D images and image/label shape mismatches, and **fails
loudly when no pair is found** — on the platform that almost always means the data-enrichment (label
upload) step was skipped (the trust PACS supplies CT images only; see
`docs/source/user-guides/user-common.rst`).

### Dataset setup (local runs)

This directory owns the shared MSD spleen download tooling (`utils/`, with its own `uv` project in
`pyproject.toml`) used by every spleen tutorial. From the repo root:

```bash
make -C fl-tutorials download-spleen-data          # NUM_CASES=<1-41> to control size (default 10)
```

which runs `utils/download_spleen_dataset.py` (downloads MSD spleen data and reorganises it so each
subject folder holds both the image and its label) followed by `utils/create_spleen_accession_csv.py`
(builds the `accession_id` dataframe the trainer reads in LOCAL_DEV). Data lands under
`fl-tutorials/nvflare/data/spleen/` (gitignored):

```text
data/spleen/
├── images/
│   ├── subject_2/
│   │   └── scans/
│   │       ├── input_spleen_2.nii.gz
│   │       └── label_spleen_2.nii.gz
│   └── ...
└── dataframe.csv
```

The downloader refuses to write into an existing output folder — delete or rename the target
directory before re-running.

## The network

MONAI UNet (`spatial_dims` read from `config.json`'s `net_config`), identical architecture — and
therefore checkpoint-compatible state dict — to the spleen evaluation tutorial.

## The training logic

DiceCE loss (`lambda_ce=0.2`, `lambda_dice=0.8`), Adam, gradient clipping at `max_norm=1.0`.
Validation runs at the end of each local epoch (`VALIDATE_EVERY` in `config.json`) with
sliding-window inference, reporting mean Dice. Global model evaluation is handled by the server
workflow — no `validator.py` needed.

### Best-model selection

`BEST_MODEL_METRIC` in `config.json` enables saving the best global model alongside the final one
(default `VAL_DICE`; set `BEST_MODEL_METRIC_MINIMIZE: true` for loss-like metrics). Each round the
trainer evaluates the *received* global model on its validation split before training and reports
`VAL_LOSS` / `VAL_DICE` on the returned `FLModel`; the server's stock `IntimeModelSelector`
averages the chosen metric across clients and saves `best_FL_global_model.pt` whenever it improves.
Round 0 is never selected (no aggregated model exists yet), so `BEST_MODEL_METRIC` requires
`GLOBAL_ROUNDS >= 2` in `config.json` — platform uploads reject the combination otherwise. Remove
both keys to skip selection (and the extra per-round validation pass).

## FLIP-specific values

`FLIP_PROJECT_ID` and `FLIP_QUERY` are read from environment variables (stubs in `.env.app`).
They are NOT passed as CLI flags because the SQL query contains spaces that don't survive
argparse whitespace-splitting. The trainer reads the query from `config_fed_client.json` at
runtime via `load_query()`. `.env.app` ships `FLIP_PROJECT_ID=dev`: LOCAL_DEV ignores the value,
but the `--project_id {project_id}` task arg needs a non-empty token to substitute.

## How to run

### Export (primary — no GPU needed)

Produces a complete NVFLARE job directory under `./fl_job/flip_fedavg/` including:
- `meta.json` with `custom_props.model_id` (dev UUID for local use)
- `app/config/config_fed_server.json` and `config_fed_client.json`
- `app/custom/` with the bundled `flip/` package and all user app files staged alongside it

```bash
make export
```

or equivalently — the Makefile runs `job.py` in the **flip-utils** environment with the `full` ML
extra (the same package set the `flare-fl-base` FL image installs via `uv sync --extra full`), so a
local run matches the deployed image and a bare `uv run` (repo-root venv, no torch/nvflare/monai) is
avoided:

```bash
uv run --project ../../../../flip-utils --extra full python job.py --export --export-dir ./fl_job --n_clients 2 --num_rounds 2
```

> **Note:** `make run` (invoked by the tutorial runner `run-tutorial`/`run-all-tutorials`)
> delegates to `make sim`. Use `make export` (no GPU) or `make sim`/`make run` (GPU + data).

### SimEnv (requires GPU + data)

Runs the job under the NVFLARE simulator with a local GPU. First download the reference dataset:

```bash
make -C fl-tutorials download-spleen-data
make sim
```

### Overriding rounds and clients

`NUM_ROUNDS` (default `2`) and `N_CLIENTS` (default `2`) parameterise both `make export` and
`make sim`/`make run`, and propagate through the tutorial harness:

```bash
make sim NUM_ROUNDS=10                                                        # 10-round local simulation
make export NUM_ROUNDS=10 N_CLIENTS=3
make -C fl-tutorials run-tutorial TUTORIAL=3d_spleen_segmentation NUM_ROUNDS=10
```

> **Local knob only.** `--num_rounds` governs local simulation and export. In production the FL API
> reads `GLOBAL_ROUNDS` from `config.json` at submit time and overrides whatever `job.py` baked into
> the exported config — deployed round counts come from `config.json`, never from these flags.

### Running against the FLIP stack

The standalone NVFLARE submit path (`make -C fl-services/nvflare submit`) is **not wired** (it is
admin-API based, unlike Flower's HTTP submit) — run locally via the simulator instead. To exercise
the full platform path, upload the app files through the FLIP UI (or `make e2e_smoke`), where the
FL API bundles the template and applies `config.json`'s `GLOBAL_ROUNDS` at submit time. Remember
the data-enrichment step: training dies with "No image/label pairs found" until the labels are
uploaded next to the pulled images in XNAT.
