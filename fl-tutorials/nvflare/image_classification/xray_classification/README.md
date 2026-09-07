# X-Ray Classification — FLIP tutorial

This tutorial trains a DenseNet-121 classifier of X-Ray pathologies (Effusion, Edema) using the
**NVFLARE Client API** (`nvflare.client`). The job is defined entirely in Python via
`FlipFedAvgRecipe` — no hand-written JSON configs required.

It replaced the retired Executor-API tutorial of the same name (removed along with the other
legacy-syntax NVFLARE tutorials); unlike that legacy flow there is no separate `validator.py` —
validation runs server-side via cross-site model evaluation.

## Data requirements

The input dataframe must contain column names matching the
`LESIONS` field in `app_files/config.json`.

Example: if `LESIONS` is `{"0": "Effusion", "1": "Edema", "-1": "Lungs in normal arrangement"}`,
the dataframe must contain a column for each of those values.

- Key `-1` is reserved for the "Normality" label. A positive value overrides every other label to 0.
- `value_to_numerical` maps the 0/1 classes to their dataframe representation (`"Yes"` / `"No"`).
- Images must be DICOM (`.dcm`), 2D grayscale, stored in folders named by accession ID.

Class imbalance can produce NaN metrics; use approximately N=300 samples per site.

## The network

DenseNet-121 pre-trained on ImageNet, implemented with MONAI.

## The training logic

Binary Cross-Entropy loss with masking of don't-care labels (`-1`). Validation runs at the end of
each local round inside the trainer (`VALIDATE_EVERY` in `config.json`). Cross-site model evaluation
is handled by the server workflow — no `validator.py` needed.

### Best-model selection

`BEST_MODEL_METRIC` in `config.json` enables saving the best global model alongside the final one
(`BEST_MODEL_METRIC_MINIMIZE: true` for loss-like metrics where lower is better). Each round the
trainer evaluates the *received* global model on its validation split before training
(`evaluate_global_model`) and reports the metrics on the returned `FLModel`; the server's stock
`IntimeModelSelector` averages the chosen metric across clients and saves
`best_FL_global_model.pt` whenever it improves. Valid labels are `VAL_LOSS`, per-lesion
`VAL-<METRIC>-<lesion>` and macro `VAL-<METRIC>` (mean across lesions) for
`F1-SCORE`/`PRECISION`/`RECALL` — the default is macro `VAL-F1-SCORE`. Remove both keys to skip
selection (and the extra per-round validation pass); the results zip then contains only the final
model. Round 0 is never selected (no aggregated model exists yet), so `BEST_MODEL_METRIC` requires
`GLOBAL_ROUNDS >= 2` in `config.json` — platform uploads reject the combination otherwise. Note
that the final model is never a selection candidate: the metric is evaluated on the global model
each client *receives*, and the last round's aggregate is never sent back out, so `best` means
best among the intermediate global models — the final model may actually outperform it.

## FLIP-specific values

`FLIP_PROJECT_ID` and `FLIP_QUERY` are read from environment variables (set stubs in `.env.app`).
They are NOT passed as CLI flags because the SQL query contains spaces that don't survive
argparse whitespace-splitting. The trainer reads the query from `config_fed_client.json` at
runtime via `load_query()`.

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
uv run --project ../../../../flip-utils --extra full python job.py --export --export-dir ./fl_job --n_clients 2 --num_rounds 3
```

> **Note:** `make run` (invoked by the tutorial runner `run-tutorial`/`run-all-tutorials`)
> delegates to `make sim`. Use `make export` (no GPU) or `make sim`/`make run` (GPU + data).

### SimEnv (requires GPU + data)

Runs the job under the NVFLARE simulator with a local GPU. First download the reference dataset:

```bash
make -C fl-tutorials download-xray-data   # pulls aicentreflip/flip-fl-base-test-data
make sim
```

FLIP-specific values (`FLIP_PROJECT_ID`, `FLIP_QUERY`) are injected via the environment or
by setting them in `.env.app` before running `make sim`.

### Overriding rounds and clients

`NUM_ROUNDS` (default `3`) and `N_CLIENTS` (default `2`) parameterise both `make export` and
`make sim`/`make run`, and propagate through the tutorial harness:

```bash
make sim NUM_ROUNDS=10                                                        # 10-round local simulation
make export NUM_ROUNDS=10 N_CLIENTS=3
make -C fl-tutorials run-tutorial TUTORIAL=xray_classification NUM_ROUNDS=10
```

or pass the flags directly using the recipe syntax:

```bash
uv run --project ../../../../flip-utils --extra full python job.py --n_clients 3 --num_rounds 10
```

> **Local knob only.** `--num_rounds` governs local simulation and export. In production the FL API
> reads `GLOBAL_ROUNDS` from `config.json` at submit time and overrides whatever `job.py` baked into
> the exported config — deployed round counts come from `config.json`, never from these flags.

### Running against the FLIP stack

The standalone NVFLARE submit path (`make -C fl-services/nvflare submit`) is **not wired** (it is
admin-API based, unlike Flower's HTTP submit) — run locally via the simulator instead. To exercise
the full platform path, upload the app files through the FLIP UI (or `make e2e_smoke`), where the
FL API bundles the template and applies `config.json`'s `GLOBAL_ROUNDS` at submit time.
