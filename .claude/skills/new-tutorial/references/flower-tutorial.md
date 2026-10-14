# Flower tutorial anatomy

A Flower tutorial is a flwr app: a `pyproject.toml` for the local simulator plus an `app/`
package that is **exactly what the user uploads**. Two of the files in `app/` are not the
tutorial's own: `server_app.py` and `strategy.py` are byte-for-byte copies of the
`fl-apps/flower/<job_type>/app/` template, because flip-api's bundler keeps the template's copy
and silently discards an uploaded one. A tutorial that edits them would run something different
on the platform than on the simulator, so the parity test refuses any difference.

## Where it lives and how it is found

```
fl-tutorials/flower/<name>/        # one level, no category
```

Discovery is `$(wildcard */app/.)` in `fl-tutorials/flower/Makefile`: a direct subdirectory with
an `app/` is a tutorial. Use the same `<name>` as the NVFLARE twin when one exists.

**One hand-written registration is mandatory:** `fl-tutorials/flower/sim-tutorial.sh` has a
`case "$TUTORIAL" in` block mapping each tutorial to its dev data. Without an entry the runner
exits with `No data mapping`, and `tests/test_sim_tutorial_exit_status.py::test_every_flower_tutorial_has_a_simulator_data_mapping`
fails. An entry exports `DEV_DATAFRAME` (always), `DEV_IMAGES_DIR` (imaging only), and sets
`DATASET_TARGET=<download target>` for the "Dataset missing" hint. All paths are under
`$DATA_ROOT` (`fl-tutorials/data/`). An evaluation tutorial additionally needs a `RUN_CONFIG`
line pointing `flip-job-dir` at the checkpoint dir and `checkpoint` at the file (the simulator
has no fl-api submit step to inject them) — copy the `3d_spleen_segmentation_evaluation` branch.

## The file set

| File | Role | Notes |
|------|------|-------|
| `pyproject.toml` | flwr app definition for `flwr run` **only** | On the platform the bundle's pyproject comes from the template. Copy from the nearest tutorial. `[project] name = "standard-app"` (or `"evaluation-app"`), `[tool.flwr.app.components]` pointing at `app.server_app:app` / `app.client_app:app`, the `[tool.flwr.app.config]` block below, the shared `[tool.ruff]` block. **Do not declare `flip-utils`** as a dependency (FLIP#767: the simulator would install a PyPI release over the checkout's copy) and **do not add `[tool.flwr.federations]`** (flwr rewrites it into `~/.flwr/config.toml` and dirties the tracked file). |
| `app/__init__.py` | Package marker | Header + one docstring, nothing else (the parity test treats a docstring-only module as inert, which is what lets it differ from the template's). |
| `app/client_app.py` | The tutorial's `ClientApp` | `@app.train(mods=[flip_local_dp_mod])` and `@app.evaluate()`. Required by `required_files.json`. |
| `app/server_app.py`, `app/strategy.py` | **Byte-identical to `fl-apps/flower/<job_type>/app/`** | `cp` them; never edit. Not symlinks (`flwr build` drops symlinks from the FAB). |
| `app/models.py` | `get_model()` — zero-arg callable | The template `server_app.py` calls it with no arguments to seed round 0; keep the signature zero-arg (defaults may read `config.json`). Required. |
| `app/config.json` | `"job_type"` + the tutorial's own hyperparameters | The **only** per-tutorial knob that rides through the upload flow. Required. |
| `app/config.toml` | Run-config overrides fl-api feeds to `flwr run --run-config` on a platform submit | Top-level keys only; may only name keys the template pyproject already declares (`num-server-rounds`, `best-model-metric`, the `dp-*` keys, `checkpoint` for evaluation). Needs `.toml` in `ALLOWED_MODEL_FILE_EXTENSIONS` (default includes it). |
| `app/task.py`, `app/transforms.py`, `app/data_loading.py`, … | Tutorial code | Flat modules inside the `app` package; import as `from app.task import …` or relative. |
| `query.sql` | The OMOP cohort query | Same rules as NVFLARE; when a twin exists keep it byte-identical and list the pair in `scripts/check_tutorial_sync.sh`. Evaluation tutorials reuse the training tutorial's query (no `query.sql` of their own). |
| `README.md` | Tutorial docs | See below. |
| `docs/<what>.png` + `docs/make_<what>_figure.py` | The README's input → output figure and its generator | Same precedent and limits as NVFLARE (`arkplus_fine_tuning/docs/`); when a twin exists, one figure script, shared the way `query.sql` is. |
| `.gitignore` | `.flwr/ .venv/ uv.lock .ruff_cache/` | Plus `*.pt` when a checkpoint is downloaded into `app/`. |
| `.env.app` + `Makefile` | **Evaluation tutorials only** | `MODEL_CHECKPOINT_URL=…` and a `download-checkpoints` target that curls the checkpoint into `app/` for the containers path. Not a discovery marker on Flower. |

### `[tool.flwr.app.config]` — what must be there

```toml
num-server-rounds = 3
dp-enabled = true            # flip.flower.privacy.LocalDpConfig reads the five dp-* keys
dp-clipping-norm = 1.0
dp-sensitivity = 1e-4
dp-epsilon = 10.0
dp-delta = 1e-5
flip-model-id = "uuid"       # placeholders; fl-api injects the real values at submit
flip-project-id = "uuid"
flip-cohort-query = "*"
flip-job-dir = "job-dir"
flip-min-clients = 2         # TOML int >= 2, at line start: sim-tutorial.sh seds it for the site count,
                             # test_flower_min_clients_wiring.py asserts it, fl-api overrides it
best-model-metric = "test_f1-score"   # a key the ClientApp reports in its evaluate MetricRecord
best-model-metric-minimize = false
```

Evaluation apps drop the `dp-*` and best-model keys, set `num-server-rounds = 1`, and add
`checkpoint = "model.pt"` plus whatever inference knobs the client reads.

### `client_app.py` contract

- Site identity: `client_identity(context)` from `flip.flower.identity` — never `os.getenv("SUPERNODE_NAME")`
  directly. The simulator sets no `SUPERNODE_NAME`; identity comes from `node_config`'s `partition-id`
  and `client_identity` handles both (the EHR Flower client still reads the env var directly and so
  hands every simulated site the full CSV — do not copy that).
- Local data: under `FlipConstants.LOCAL_DEV` every client sees the same `DEV_DATAFRAME`, so slice
  it with `partition_cohort(df, context)` (keys on `person_id` or `accession_id`; a no-op when
  deployed). Then `check_splits_are_populated(...)` before touching images, so a starved partition
  fails with a clear error instead of an empty loader.
- Data access only through `flip.FLIP` (`get_dataframe`, `get_by_accession_number`).
- Imports from `flwr.app` (`ArrayRecord`, `ConfigRecord`, `Context`, `Message`, `MetricRecord`,
  `RecordDict`), `flwr.clientapp` (`ClientApp`), `flwr.common` (`log`) — `test_flwr_import_paths.py`
  resolves every `from flwr.X import …` against the installed flwr.
- Replies: `RecordDict({"arrays": …, "metrics": MetricRecord(...), "config": ConfigRecord({"site": client_name})})`.
  Metric keys follow `<label>[@<x_label>][.x_<N>]` (`flip.flower.metrics`); the key named in
  `best-model-metric` must appear in the evaluate reply. `num-examples` is bookkeeping, keep it.
- Hyperparameters come from `app/config.json`, loaded relative to `__file__`.

## Reference tutorials to copy from

| New tutorial is… | Start from |
|------------------|------------|
| Tabular / OMOP-only, CPU | `ehr_risk_prediction` (but take identity from `client_identity`, see above) |
| 2-D imaging classification | `xray_classification` |
| 3-D segmentation with enrichment labels | `3d_spleen_segmentation`; `3d_prostate_segmentation` for multi-series studies + nnU-Net plan |
| Evaluation of an uploaded checkpoint | `3d_spleen_segmentation_evaluation` |

## Guards that see a new Flower tutorial automatically

- `tests/test_flower_platform_parity.py`: `app/config.json` exists with a `job_type` that names an
  `fl-apps/flower/<job_type>/` template; every file in `required_files.json[job_type]`
  (`client_app.py`, `config.json`, `models.py`) exists; every template `app/*.py` is present and
  byte-identical (inert `__init__.py` excepted).
- `tests/test_flower_min_clients_wiring.py`: `flip-min-clients` is a TOML int >= 2 (skipped for a
  pyproject without `flip-model-id`); the server-side checks pass as long as the template
  `server_app.py` is unmodified.
- `tests/test_flwr_import_paths.py`: flwr import paths resolve.
- `tests/test_sim_tutorial_exit_status.py`: the `sim-tutorial.sh` data mapping exists.
- `tests/test_offline_apps.py`: an `app/` with Python files; no run-time downloads; `weights_only=True`.
- `make -C fl-tutorials lint`: ruff with the per-tutorial `[tool.ruff]` block.

By hand: `DICOM_APPS` in `tests/tutorial_apps.py` for a 2-D DICOM/MONAI `LoadImaged` app; a
`test_<tutorial>_*.py` at `tests/` root for app-specific logic (prostate has four); a
`check_tutorial_sync.sh` pair for files shared byte-for-byte with the NVFLARE twin.

## README shape (Flower)

Every README opens with the task shown, not told: an input → output figure from the tutorial's
dataset (committed PNG under `docs/`, regenerated by `docs/make_<what>_figure.py` — see SKILL.md §5)
and, for a non-FedAvg loop, a Mermaid flow block. "Expected performance" carries a reference
training curve from a simulator run plus the numbers, rounds-to-plateau and failure signatures in
prose; the README ends with `## References` (method paper with BibTeX, dataset + licence,
checkpoint source) — SKILL.md §5.

Title `# <Use case> — FLIP tutorial (Flower)` (or `(Flower)` suffix). Recurring sections:
Layout / Folder structure (annotate which files come from the template) · Configuration: two files
(`config.json` vs `config.toml`, and why `flip-utils` is undeclared) · The model and cohort ·
Metrics and best-model selection · Differential privacy · Data · How to run (`### Local run (the
flwr simulator)` with `make -C fl-tutorials run-tutorial TUTORIAL=<name> FL_BACKEND=flower`;
`### On the platform (or \`make e2e_smoke\`)` naming the upload dir `app/`, the job type, the
imaging toggle and the e2e target).
