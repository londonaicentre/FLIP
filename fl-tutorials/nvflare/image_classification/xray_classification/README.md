# Chest X-ray lesion classification

This tutorial trains a DenseNet-121 classifier of X-Ray pathologies (Effusion, Edema) using the
**FLARE Client API** (`nvflare.client`). The job is defined entirely in Python via
`FlipFedAvgRecipe` — no hand-written JSON configs required.

## At a glance

| Item | Value |
|---|---|
| What you upload | everything in `app_files/` |
| Job type | standard |
| Imaging | on, DICOM to NIfTI off |
| Cohort query | `query.sql` (`query_novocab.sql` for a trust without the OMOP vocabulary) |
| Dataset | `make -C fl-tutorials download-xray-data` |
| Simulator | `make -C fl-tutorials run-tutorial TUTORIAL=xray_classification` (GPU) |

## Data requirements

The input dataframe must contain column names matching the
`LESIONS` field in `app_files/config.json`.

Example: if `LESIONS` is `{"0": "Effusion", "1": "Edema", "-1": "Lungs in normal arrangement"}`,
the dataframe must contain a column for each of those values.

- Key `-1` is reserved for the "Normality" label. A positive value overrides every other label to 0.
- `value_to_numerical` maps the 0/1 classes to their dataframe representation (`"No"` / `"Yes"`).
- Images must be DICOM (`.dcm`), 2D grayscale, stored in folders named by accession ID.
- The app reads DICOM only, so create its project with **DICOM to NIfTI** off: conversion would produce NIfTI nothing reads.
- Use roughly 300 samples per site: class imbalance can produce NaN metrics.

## The network

DenseNet-121 implemented with MONAI, trained **from scratch** (`pretrained=False`).

FL apps must never download anything at run time (FLIP#1206). On a platform-managed estate the FL
server has no internet route, and neither does a trust host behind an NHS firewall. A run-time
download would also bypass the scanned upload path, so the file a Trust inspects before training
would not be the file that runs. The canonical statement of this rule is the *Model files* section
of the [user guide](../../../../docs/source/user-guides/user-models.rst).

Training from scratch costs nothing measurable here:

- With this 1-channel, 128-feature DenseNet, only 290 of 727 tensors (about 30 % of the parameters, all bottleneck `norm2`/`conv2`) matched the ImageNet shapes. The first convolution and every growth layer were random either way.
- On the reference mini dataset (2 clients, 3 rounds × 3 local epochs, two runs each), final global-model test F1 was 0.976/0.965 with the partial ImageNet init and 0.962/0.966 without. Both converged by epoch 5–6.

An app that does need pretrained weights ships them as an uploaded file. The [brain MRI autoencoder tutorial](../../image_generation/brain_mri_ldm_stage1_autoencoder/README.md) shows the pattern (`make weights`).

## The training logic

Binary Cross-Entropy loss with masking of don't-care labels (`-1`). Validation runs at the end of
each local round inside the trainer (`VALIDATE_EVERY` in `config.json`). Cross-site model evaluation
is handled by the server workflow — no `validator.py` needed.

### Best-model selection

`BEST_MODEL_METRIC` in `config.json` saves the best global model alongside the final one. Set `BEST_MODEL_METRIC_MINIMIZE: true` for loss-like metrics, where lower is better.

How it works: each round, the trainer evaluates the global model it *received* on its validation split before training (`evaluate_global_model`), and reports the metrics on the returned `FLModel`. The server's stock `IntimeModelSelector` averages the chosen metric across clients and saves `best_FL_global_model.pt` whenever it improves.

- **Valid metric labels:** `VAL_LOSS`; per-lesion `VAL-<METRIC>-<lesion>`; and macro `VAL-<METRIC>` (mean across lesions), where `<METRIC>` is `F1-SCORE`, `PRECISION` or `RECALL`. The default is macro `VAL-F1-SCORE`.
- **Turning it off:** remove both keys. This also skips the extra per-round validation pass, and the results zip then contains only the final model.
- **Requires `GLOBAL_ROUNDS >= 2`** in `config.json`. Round 0 is never selected because no aggregated model exists yet, and platform uploads reject the combination otherwise.
- **The final model is never a candidate.** The metric is evaluated on the global model each client *receives*, and the last round's aggregate is never sent back out. So `best` means best among the intermediate global models, and the final model may outperform it.

## FLIP-specific values

`FLIP_PROJECT_ID` and `FLIP_QUERY` are read from environment variables. For local runs, set stubs in
`.env.app` or export them before `make sim`. They are not CLI flags, because the SQL query contains
spaces that argparse would split. `job.py` passes them to the recipe, which writes the query into the
generated `config_fed_client.json`. At run time the trainer reads it from there via `load_query()`.

## How to run

### Export (primary — no GPU needed)

Produces a complete FLARE job directory under `./fl_job/flip_fedavg/` including:
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
uv run --project ../../../../flip-utils --extra full python job.py --export --export-dir ./fl_job --n_clients 2 --num_rounds 3
```

> **Note:** `make run` (what `run-tutorial`/`run-all-tutorials` invoke) is the same as `make sim`, and needs a GPU and data. `make export` needs neither.

### SimEnv (requires GPU + data)

Runs the job under the FLARE simulator with a local GPU. First download the reference dataset:

```bash
make -C fl-tutorials download-xray-data   # from the repo root; pulls aicentreflip/flip-fl-base-test-data
make sim                                  # from this tutorial directory
```

### Overriding rounds and clients

`NUM_ROUNDS` (default `3`) and `N_CLIENTS` (default `2`) parameterise both `make export` and
`make sim`/`make run`, and propagate through the tutorial harness:

```bash
make sim NUM_ROUNDS=10                    # this directory: 10-round local simulation
make export NUM_ROUNDS=10 N_CLIENTS=3     # this directory
make -C fl-tutorials run-tutorial TUTORIAL=xray_classification NUM_ROUNDS=10   # repo root
```

or pass the flags directly using the recipe syntax:

```bash
uv run --project ../../../../flip-utils --extra full python job.py --n_clients 3 --num_rounds 10
```

> **Local knob only.** `--num_rounds` governs local simulation and export. In production the FL API
> reads `GLOBAL_ROUNDS` from `config.json` at submit time and overrides whatever `job.py` baked into
> the exported config — deployed round counts come from `config.json`, never from these flags.

### Running against the FLIP stack

The standalone FLARE submit target (`make -C fl-services/nvflare submit`) is **not wired**: FLARE
submits through its admin API rather than over HTTP as Flower does. Run locally via the simulator instead. To exercise
the full platform path, upload the app files through the FLIP UI (or `make e2e_smoke`), where the
FL API bundles the template and applies `config.json`'s `GLOBAL_ROUNDS` at submit time.
