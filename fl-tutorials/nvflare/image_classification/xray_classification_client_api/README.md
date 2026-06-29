# X-Ray Classification — NVFLARE Client API variant

This tutorial trains a DenseNet-121 classifier of X-Ray pathologies (Effusion, Edema) using the
**NVFLARE Client API** (`nvflare.client`) rather than the legacy Executor API. The job is defined
entirely in Python via `FlipFedAvgRecipe` — no hand-written JSON configs required.

This is the sibling tutorial to `xray_classification/`, which uses the legacy Executor API (with
a separate `validator.py`). In this variant, validation runs server-side via cross-site model
evaluation — there is no `validator.py`.

## Data requirements

Identical to the standard xray tutorial. The input dataframe must contain column names matching the
`LESIONS` field in `app_files/config.json`.

Example: if `LESIONS` is `{"0": "Effusion", "1": "Edema", "-1": "Lungs in normal arrangement"}`,
the dataframe must contain a column for each of those values.

- Key `-1` is reserved for the "Normality" label. A positive value overrides every other label to 0.
- `value_to_numerical` maps the 0/1 classes to their dataframe representation (`"Yes"` / `"No"`).
- Images must be DICOM (`.dcm`), 2D grayscale, stored in folders named by accession ID.

Class imbalance can produce NaN metrics; use approximately N=300 samples per site.

## The network

DenseNet-121 pre-trained on ImageNet, implemented with MONAI (same as the standard tutorial).

## The training logic

Binary Cross-Entropy loss with masking of don't-care labels (`-1`). Validation runs at the end of
each local round inside the trainer (`VALIDATE_EVERY` in `config.json`). Cross-site model evaluation
is handled by the server workflow — no `validator.py` needed.

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

or equivalently:

```bash
uv run --no-sync python job.py --export --export-dir ./fl_job --n_clients 2 --num_rounds 3
```

> **Note:** `make run` (invoked by the tutorial harness `run-tutorial`/`run-all-tutorials`)
> delegates to `make sim` for the Client API variant. The legacy single-app `testing/` harness
> used by `xray_classification` is intentionally **not** used here: it mounts only `./tmp/app`
> and has no `meta.json`, so `FlipFedAvgRecipe` components cannot resolve `model_id`. Use
> `make export` (no GPU) or `make sim`/`make run` (GPU + data) instead.

### SimEnv (requires GPU + data)

Runs the job under the NVFLARE simulator with a local GPU. First download the reference dataset:

```bash
make -C fl-tutorials download-xray-data   # pulls aicentreflip/flip-fl-base-test-data
make sim
```

FLIP-specific values (`FLIP_PROJECT_ID`, `FLIP_QUERY`) are injected via the environment or
by setting them in `.env.app` before running `make sim`.

### Submitting to a running FL cluster

After `make export`, submit the produced job directory to the FLIP stack:

```bash
make -C fl-services/nvflare submit APP=./fl_job/flip_fedavg
```
