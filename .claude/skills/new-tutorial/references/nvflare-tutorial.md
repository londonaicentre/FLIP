# NVFLARE tutorial anatomy

An NVFLARE tutorial is a loose directory of scripts, not a Python project. It runs in the
**flip-utils venv** (`uv run --project flip-utils --extra full`, the package set the FL image
installs), so it has no `pyproject.toml` and no venv of its own. It copies nothing from
`fl-apps/nvflare/`: the server/client configs the platform would bundle around the uploaded
files are regenerated locally by `job.py` from the same recipe class.

## Where it lives and how it is found

```
fl-tutorials/nvflare/<category>/<name>/      # two levels: category/name
```

Discovery is `$(wildcard */*/.env.app)` in `fl-tutorials/nvflare/Makefile`: a directory counts
as a tutorial **iff** it sits two levels down and carries a `.env.app`. `run-tutorial` matches
`TUTORIAL=` against the basename only, so the name must be unique across categories and across
backends (the Flower twin uses the same name; the e2e harness and docs assume that).

Existing categories: `image_classification`, `image_segmentation`, `image_evaluation`,
`image_synthesis`, `tabular_classification`. Reuse one; add a category only for a new task
family, and keep the `<verb>_<noun>` form.

## The file set

| File | Role | Notes |
|------|------|-------|
| `Makefile` | `run` (= `sim`), `sim`, `export`, `clean` | The harness only needs `run`. Copy from the nearest tutorial; the `.env.app` include block and the `$(abspath …)` of every data path are load-bearing (simulator workers run from the workspace dir). `NUM_ROUNDS ?=` / `N_CLIENTS ?=` are the local knobs. |
| `.env.app` | Marks the dir as a tutorial; local-run env | Keys: `JOB_TYPE=<standard\|evaluation\|diffusion_model\|fed_opt>`, data paths relative to the tutorial (`../../../data/<dataset>/…`), `FLIP_PROJECT_ID=` and `FLIP_QUERY=` left empty, a commented `# NUM_ROUNDS=` / `# N_CLIENTS=` block. No licence header. |
| `job.py` | Builds the FLIP recipe, stages `app_files/`, runs `SimEnv` or exports | Copy verbatim from `tabular_classification/ehr_risk_prediction/job.py` and change: the docstring, the argparse description, `--num_rounds` default, `--workspace` default (`/tmp/nvflare/<short>`), and the recipe class if the job type is not `standard`. Do not declare `--export`/`--export-dir` (Recipe.execute strips them). |
| `app_files/` | **Everything the user uploads** | Flat; imported flat at run time (no package, no relative imports). The platform bundles exactly these files into `app/custom/`. |
| `query.sql` | The OMOP cohort query | Not uploaded; saved on the project. `--` Apache header. Imaging cohorts must project `accession_id`; every cohort should project `person_id` (the disclosure-threshold gate counts distinct subjects). Deterministic `ORDER BY` + literal `LIMIT`. Validated trust-side by `data_access_api.services.cohort.validate_query`: single SELECT, `omop` schema, no DML anywhere in the tree. |
| `README.md` | The tutorial's own docs | See the README section below. |
| `.gitignore` | `fl_job/` | The `export` output. |
| `docs/<what>.png` + `docs/make_<what>_figure.py` | The README's input → output figure and its generator | Precedent: `image_classification/arkplus_fine_tuning/docs/`. PNG < 1000 KB; script validates panels against the cohort dataframe; runs under a `docs` extra / `make <what>-figure DATA_ROOT=`. |
| `export/metadata.json` | Optional | MONAI-bundle `network_data_format`; only needed if the model is to be packaged as a MAP (`docs/source/working-with-flip-apps/package-model-as-map.rst` looks for it at `<app-dir>/../export/`). |

### `app_files/` by job type

The required set per job type is `fl-apps/nvflare/<template>/required_files.json` (flip-api
refuses a model upload missing one):

| `JOB_TYPE` | Recipe class | Required files |
|------------|--------------|----------------|
| `standard` | `FlipFedAvgRecipe` | `trainer.py`, `config.json`, `models.py` |
| `fed_opt` | `FlipFedOptRecipe` | `trainer.py`, `config.json`, `models.py` |
| `evaluation` | `FlipEvalRecipe` | `evaluator.py`, `config.json`, `models.py` |
| `diffusion_model` | `FlipDiffusionRecipe` | `trainer.py`, `validator.py`, `config.json`, `models.py` |

Everything else in `app_files/` is the tutorial's own (e.g. `transforms.py`, `data_utils.py`,
`feature_engineering.py`, `loss_and_metrics.py`).

- `models.py` exposes `get_model()`; the recipe's persistor/locator reference it as
  `models.get_model`, which is why `job.py` puts `app_files/` on `sys.path` before importing the recipe.
- `trainer.py` is an NVFLARE Client-API loop: `flare.init()`, `while flare.is_running()`,
  `receive()`, branch on `is_train()` / `is_evaluate()` / `is_submit_model()`, `send()`. Data comes
  **only** through `flip.FLIP` (`get_dataframe(project_id, query)`, `get_by_accession_number(...)`).
  The query is read from `../config/config_fed_client.json` at run time (`load_query()`), the
  project id arrives as `--project_id {project_id}`.
- `config.json` carries `job_type` plus the hyperparameters the trainer reads
  (`LOCAL_ROUNDS`, `GLOBAL_ROUNDS`, `LR_START`/`LR_END`, `VAL_SPLIT`, `TEST_SPLIT`, `BATCH_SIZE`,
  `VALIDATE_EVERY`, `SEED`, …) and the best-model keys `BEST_MODEL_METRIC` /
  `BEST_MODEL_METRIC_MINIMIZE` that `job.py` passes to the recipe. On the platform the FL API reads
  `GLOBAL_ROUNDS` from here at submit time, overriding whatever the recipe baked in.
- Local vs platform data: branch on `flip.constants.FlipConstants.LOCAL_DEV`. Deployed → `FLIP()`
  calls; locally → `SITE{N}_DATAFRAME` / `SITE{N}_IMAGES_DIR` per simulated site, falling back to
  `DEV_DATAFRAME` / `DEV_IMAGES_DIR` sliced per site (ehr does `person_id` modulo
  `DEV_NUM_PARTITIONS`). Any simulator-only knob (`MAX_SAMPLES` in the Ark+ apps) must live in that
  `LOCAL_DEV` branch only, so a deployed job is never affected.

## The recipe API (`flip-utils/flip/nvflare/recipes/`)

`FlipFedAvgRecipe(num_rounds, min_clients, train_script="trainer.py",
train_args="--project_id {project_id}", project_id, query, local_rounds, model_id,
percentile_privacy, heart_beat_timeout, validation_timeout, params_exchange_format="numpy",
params_transfer_type="FULL", aggregate_only_regex, best_model_metric, best_model_metric_minimize)`.
`FlipFedOptRecipe` adds `optimizer_args`, `lr_scheduler_args`, `device`. `FlipEvalRecipe` takes
`eval_script="evaluator.py"` instead of training args. `FlipDiffusionRecipe` takes `num_rounds_ae`
+ `num_rounds_dm`. All expose `.job` (the `FedJob`) for `stage_app_files`.

## Reference tutorials to copy from

| New tutorial is… | Start from |
|------------------|------------|
| Tabular / OMOP-only, CPU | `tabular_classification/ehr_risk_prediction` (smallest; also shows per-site partitioning) |
| 2-D imaging classification | `image_classification/xray_classification` |
| 3-D segmentation (NIfTI via XNAT, labels as enrichment) | `image_segmentation/3d_spleen_segmentation` |
| Evaluation of an uploaded checkpoint | `image_evaluation/3d_spleen_segmentation_evaluation` |
| Fine-tuning from a shipped checkpoint | `image_classification/arkplus_fine_tuning` (`SERVER_CHECKPOINT`, `AGGREGATE_ONLY_REGEX`) |
| Generative / two-stage | `image_synthesis/latent_diffusion_model` |

## Tests that see a new NVFLARE tutorial automatically

- `tests/test_offline_apps.py`: every `.env.app` root must have an `app_files/` with Python files
  (`test_every_tutorial_has_a_guarded_app_dir`); no run-time downloads; every `torch.load` with
  `weights_only=True`.
- `make -C fl-tutorials lint` runs ruff over the tree with the repo-root rule set.

Tests you add by hand:

- A 2-D DICOM app read through MONAI `LoadImaged` → register it in `tests/tutorial_apps.py`
  `DICOM_APPS` (the orientation suite then covers it). 3-D NIfTI apps are deliberately absent.
- App-file unit tests mirror the path: `tests/nvflare/<category>/<name>/app_files/test_<module>.py`,
  loaded through `tutorial_apps.load_module` (the apps are not packages). If the test basename
  collides with another tutorial's, give the tutorial dir and its `app_files/` an `__init__.py`
  (the Ark+ pattern) — never `nvflare/` or `<category>/`, which would shadow the real `nvflare` package.
- Shared code copied byte-for-byte from the Flower twin (or another tutorial) → add the pair to
  `scripts/check_tutorial_sync.sh` `PAIRS` and widen the `paths:` filter in
  `.github/workflows/fl-apps-check-tutorial-sync.yml` if the new paths are outside the ones it watches.

## README shape (NVFLARE)

Every README opens with the task shown, not told: an input → output figure from the tutorial's
dataset (committed PNG under `docs/`, regenerated by `docs/make_<what>_figure.py` — see SKILL.md §5)
and, for a non-FedAvg loop, a Mermaid flow block. "Expected performance" carries a reference
training curve from a simulator run plus the numbers, rounds-to-plateau and failure signatures in
prose; the README ends with `## References` (method paper with BibTeX, dataset + licence,
checkpoint source) — SKILL.md §5.

Title `# <Use case> — FLIP tutorial (NVFLARE)`. The ehr README is the fullest template:
The use case · Data (local CSV vs on the platform/OMOP) · Features and label / preprocessing ·
The model · Metrics and best-model selection · Expected performance · How to run (`make export`,
local simulation via `make -C fl-tutorials run-tutorial TUTORIAL=<name>`, on the platform / `make
e2e_smoke`, FLIP-specific values) · Honest caveats. The "on the platform" section says which dir
to upload (`app_files/`), which job type, whether to turn imaging off (`has_imaging=false` for
tabular cohorts), and which e2e target drives it.
