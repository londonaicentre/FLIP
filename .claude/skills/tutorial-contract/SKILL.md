---
name: tutorial-contract
description: The contract every FLIP federated-learning tutorial under fl-tutorials/ meets (NVFLARE, Flower, or both) — what a tutorial is, which existing tutorial to copy for each shape, the per-backend file sets and their traps, the imaging / tabular / evaluation / fine-tune shapes, how to verify, and what moves when flip-utils, an fl-apps template or a guard changes. Use it whenever the user wants to add, port, clone, review, fix up or "make a tutorial for" a model, dataset or use case (segmentation, classification, EHR/tabular, evaluation, diffusion), asks what files a tutorial needs or what its README should show, or asks why a tutorial is missing from list-tutorials, run-tutorial, the simulator or the demo recorder — even if they don't say "tutorial" but describe an example app for fl-tutorials. /new-tutorial and /audit-tutorial load it.
license: Apache-2.0
# Claude Code field, not part of the Agent Skills spec (skills-ref flags it): keeps the / menu to the two
# commands that load this skill (/new-tutorial, /audit-tutorial); other Agent-Skills tools ignore it.
user-invocable: false
---

<!--
    Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at
        http://www.apache.org/licenses/LICENSE-2.0
    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.
-->
# The FLIP tutorial contract

A tutorial is a complete, runnable example app for one FL backend: the exact files a user would
upload through FLIP, a cohort query, a way to run it on that backend's **simulator** without
containers, and a README. Most of the contract is enforced by the CPU-only guards in
`fl-tutorials/tests/` (`make -C fl-tutorials test`); the rest is a handful of hand-maintained lists
that silently leave a tutorial out when forgotten. This file is the map of both. Two lookups sit
beside it: `references/readme-shape.md` (what the README shows: task figure, reference results,
citations, demo video) and `references/registration-checklist.md` (every list a tutorial must be
added to).

## 1. What settles a tutorial's shape

Settle these with the user, or state your assumption, before copying anything — each changes the
file set:

1. **Backend(s).** New tutorials usually ship on both, under the same `<name>`; single-backend is
   fine when the user says so (prostate is Flower-only; two-stage diffusion is NVFLARE-only).
2. **Job type** — one of the four `fl-apps` templates: `standard` (FedAvg training), `evaluation`
   (no training, an uploaded checkpoint), `fed_opt`, `diffusion_model` (NVFLARE only). It fixes the
   required files and the recipe / template.
3. **Data.** 2-D DICOM through MONAI, 3-D NIfTI converted by XNAT with labels as enrichment, or
   tabular / OMOP-only (`has_imaging=false` project, CPU is enough).
4. **Dataset.** Reuse one under `fl-tutorials/datasets/` when the cohort fits. A new dataset is a
   separate deliverable; one that must also exist **on the platform** (OMOP tables, seeding, a
   published data version) is a large one — say so rather than fold it in. Access-controlled or
   private data (a Data Use Agreement, a trust's own cohort) changes what the dataset target may do
   and what the README may show; scope it with the user before writing anything.
5. **Platform wiring.** An `e2e_smoke_<name>` target only when the cohort is seeded on the dev
   trusts; a demo-video `APPS` profile the same.
6. **Prior work.** Before designing the loop, search for existing work that federates this kind
   of workflow (method + "federated", task + "federated learning", the acronyms), read what each
   client sends and how the server combines it, build on the closest one and cite it in
   `## References`, saying where the tutorial departs from it.

FLIP's plumbing is horizontal FedAvg — a size-weighted mean of client arrays (the NVFLARE recipes;
the Flower template `server_app.py` / `strategy.py`, which are fixed). A method that cannot be
expressed as weighted averaging needs a design note before any code.

**Never write a tutorial from a blank file.** Copy the nearest one (§2), rename, then edit: keep
what is structural (Makefile, `job.py`, `pyproject.toml`, the template files, the README skeleton)
and replace what is the use case (model, transforms, cohort, hyperparameters, prose). Preserve the
comments in the copied structural files — each explains a trap that has bitten once.

## 2. Copy from

| New tutorial is… | NVFLARE | Flower |
|------------------|---------|--------|
| Tabular / OMOP-only, CPU | `tabular_classification/ehr_risk_prediction` (smallest; per-site partitioning) | `ehr_risk_prediction` |
| 2-D imaging classification | `image_classification/xray_classification` | `xray_classification` |
| 3-D segmentation (NIfTI via XNAT, labels as enrichment) | `image_segmentation/3d_spleen_segmentation` | `3d_spleen_segmentation`; `3d_prostate_segmentation` for multi-series studies + an nnU-Net plan |
| Evaluation of an uploaded checkpoint | `image_evaluation/3d_spleen_segmentation_evaluation`; `arkplus_*_evaluation` for several checkpoints | `3d_spleen_segmentation_evaluation` |
| Fine-tuning from a shipped checkpoint | `image_classification/arkplus_fine_tuning` (`SERVER_CHECKPOINT`, `AGGREGATE_ONLY_REGEX`) | — |
| Generative / two-stage | `image_synthesis/latent_diffusion_model` | — |

## 3. NVFLARE: the file set and its traps

A loose directory of scripts, not a Python project; it runs in the **flip-utils venv** (`uv run
--project flip-utils --extra full`, the package set the FL image installs) and copies nothing from
`fl-apps/nvflare/`: `job.py` regenerates the server/client configs from the same recipe class.

Layout `fl-tutorials/nvflare/<category>/<name>/`. Discovery is `$(wildcard */*/.env.app)`: a
directory counts as a tutorial **iff** it sits two levels down and carries a `.env.app`.
`run-tutorial` matches `TUTORIAL=` on the basename, so names are unique across categories and
backends. Reuse a category (`image_classification`, `image_segmentation`, `image_evaluation`,
`image_synthesis`, `tabular_classification`); never put an `__init__.py` in `nvflare/` or a
category dir — a package named `nvflare` shadows the real one.

| File | Role / trap |
|------|-------------|
| `.env.app` | The discovery marker and local-run env: `JOB_TYPE=…`, data paths relative to the tutorial (`../../../data/<dataset>/…`; `DEV_DATAFRAME` / `DEV_IMAGES_DIR`, or `SITE{N}_…` per site), `FLIP_PROJECT_ID=` and `FLIP_QUERY=` left empty, a commented `# NUM_ROUNDS=` / `# N_CLIENTS=` block. No licence header. |
| `job.py` | Builds the FLIP recipe, stages `app_files/`, runs `SimEnv` or exports. Copy from `ehr_risk_prediction/job.py`; change the docstring, `--num_rounds` / `--workspace` defaults (`/tmp/nvflare/<short>`), and the recipe class. Do **not** declare `--export` / `--export-dir` — `Recipe.execute` strips them. |
| `app_files/` | Everything the user uploads, flat (no package, no relative imports); the platform bundles exactly these into `app/custom/`. Required per job type: `fl-apps/nvflare/<job_type>/required_files.json` (flip-api refuses an upload missing one). |
| `Makefile` | `run` (= `sim`), `sim`, `export`, `clean`. The `.env.app` export line and `$(abspath …)` on every data path are load-bearing: simulator workers run from the workspace dir. |
| `query.sql` | Not uploaded; saved on the project. `--` Apache header. Single `SELECT` over `omop.*`, deterministic `ORDER BY`, literal `LIMIT`; `person_id` (the disclosure floor counts subjects) and `accession_id` for imaging. Byte-identical to the Flower twin's and pinned in `scripts/check_tutorial_sync.sh`. |
| `.gitignore` | `fl_job/` (the export output). |
| `docs/` | The README figure + its generator (`references/readme-shape.md`). |
| `export/metadata.json` | Only when the model is to be packaged as a MAP. |

- `models.py` exposes `get_model()`; the recipe's persistor references it as `models.get_model`,
  which is why `job.py` puts `app_files/` on `sys.path` before importing the recipe.
- `trainer.py` is a Client-API loop (`flare.init()`, `while flare.is_running()`, `receive()`,
  `is_train()` / `is_evaluate()` / `is_submit_model()`, `send()`). Data only through `flip.FLIP`
  (`get_dataframe`, `get_by_accession_number`); the query is read from
  `../config/config_fed_client.json` at run time, the project id arrives as `--project_id`.
- `config.json` carries `job_type`, the hyperparameters the trainer reads (`LOCAL_ROUNDS`,
  `GLOBAL_ROUNDS`, `LR_START`/`LR_END`, splits, `BATCH_SIZE`, `SEED`, …) and `BEST_MODEL_METRIC` /
  `BEST_MODEL_METRIC_MINIMIZE`, which `job.py` passes to the recipe. On the platform the FL API reads
  `GLOBAL_ROUNDS` at submit time, overriding the recipe.
- Local vs platform: branch on `flip.constants.FlipConstants.LOCAL_DEV`; deployed → `FLIP()` calls,
  locally → the `.env.app` paths, sliced per site (ehr: `person_id` modulo `DEV_NUM_PARTITIONS`).
  Any simulator-only knob (`MAX_SAMPLES` in the Ark+ apps) lives in that branch only.
- Recipes (`flip-utils/flip/nvflare/recipes/`): `FlipFedAvgRecipe(num_rounds, min_clients,
  train_script, train_args, project_id, query, local_rounds, model_id, percentile_privacy, …,
  aggregate_only_regex, best_model_metric, best_model_metric_minimize)`; `FlipFedOptRecipe` adds
  `optimizer_args` / `lr_scheduler_args`; `FlipEvalRecipe` takes `eval_script="evaluator.py"`;
  `FlipDiffusionRecipe` takes `num_rounds_ae` + `num_rounds_dm`. All expose `.job` for `stage_app_files`.
- Tests you add by hand: a 2-D DICOM app read through MONAI `LoadImaged` → `tests/tutorial_apps.py`
  `DICOM_APPS` (the orientation suite then covers it); app-file unit tests mirror the path
  (`tests/nvflare/<category>/<name>/app_files/test_<module>.py`, loaded through `load_module`; an
  `__init__.py` at the tutorial dir and `app_files/` only when basenames collide — the Ark+ pattern).

## 4. Flower: the file set and its traps

A flwr app: `pyproject.toml` for the local simulator plus an `app/` package that is **exactly what
the user uploads**. Layout `fl-tutorials/flower/<name>/` (flat; #1500 moves it to
`<category>/<name>/`); discovery is a direct subdirectory carrying `app/`. Same `<name>` as the
NVFLARE twin.

Two files in `app/` are not the tutorial's own: `server_app.py` and `strategy.py` are byte-for-byte
copies of `fl-apps/flower/<job_type>/app/` (`cp`, never symlink — `flwr build` drops symlinks),
because flip-api's bundler keeps the template's copy and silently discards an uploaded one; the
parity test refuses any difference.

| File | Role / trap |
|------|-------------|
| `pyproject.toml` | For `flwr run` only (the platform bundle's comes from the template). `[project] name = "standard-app"` / `"evaluation-app"`, `[tool.flwr.app.components]` → `app.server_app:app` / `app.client_app:app`, the config block below, the shared `[tool.ruff]`. **Do not declare `flip-utils`** (FLIP#767: the simulator would install a PyPI release over the checkout) and **no `[tool.flwr.federations]`** (flwr rewrites it into `~/.flwr/config.toml` and dirties the file). |
| `app/__init__.py` | Header + one docstring, nothing else (that is what lets it differ from the template's). |
| `app/client_app.py` | `@app.train(mods=[flip_local_dp_mod])` and `@app.evaluate()`. Required. |
| `app/models.py` | `get_model()` as a **zero-argument** callable — the template server seeds round 0 with it. Required. |
| `app/config.json` | `"job_type"` + the tutorial's own hyperparameters — the only per-tutorial knob that rides through the upload. Required. |
| `app/config.toml` | Run-config overrides fl-api feeds to `flwr run --run-config` on a platform submit: top-level keys the template pyproject already declares (`num-server-rounds`, `best-model-metric`, `dp-*`, `checkpoint`). |
| `query.sql` | Same rules as NVFLARE; evaluation tutorials reuse the training tutorial's (none of their own). |
| `.gitignore` | `.flwr/ .venv/ uv.lock .ruff_cache/`, plus `*.pt` when a checkpoint is downloaded into `app/`. |
| `.env.app` + `Makefile` | Evaluation tutorials only: `MODEL_CHECKPOINT_URL=…` and a `download-checkpoints` target. Not a discovery marker on Flower. |

`[tool.flwr.app.config]` must hold: `num-server-rounds`; the five `dp-*` keys with `dp-enabled =
true` (`flip.flower.privacy.LocalDpConfig`); the `flip-model-id` / `flip-project-id` /
`flip-cohort-query` / `flip-job-dir` placeholders fl-api overrides at submit; `flip-min-clients = 2`
as a TOML int ≥ 2 at line start (`sim-tutorial.sh` seds it for the site count, the min_clients guard
asserts it, fl-api overrides it — it is a quorum, never a partition count); `best-model-metric` /
`best-model-metric-minimize`, a key the evaluate reply reports. Evaluation apps drop the `dp-*` and
best-model keys, set `num-server-rounds = 1` and add `checkpoint = "model.pt"`.

`client_app.py` contract: identity from `client_identity(context)` (`flip.flower.identity`) —
**never `os.getenv("SUPERNODE_NAME")`**, which the simulator does not set, so every simulated site
would get the whole cohort; local data sliced with `partition_cohort(df, context)` (keys on
`person_id` / `accession_id`; a no-op when deployed; raises when the site cannot be identified),
then `check_splits_are_populated(...)` before touching images; data only through `flip.FLIP`;
imports from `flwr.app` / `flwr.clientapp` / `flwr.common` (the import-paths guard resolves them);
replies `RecordDict({"arrays", "metrics": MetricRecord(...), "config": ConfigRecord({"site": name})})`
with metric keys `<label>[@<x_label>][.x_<N>]` and the `best-model-metric` key present; `num-examples`
kept; hyperparameters from `app/config.json` relative to `__file__`.

**One hand-written registration is mandatory:** the `case "$TUTORIAL" in` block of
`fl-tutorials/flower/sim-tutorial.sh` maps each tutorial to its dev data (`DEV_DATAFRAME` always,
`DEV_IMAGES_DIR` for imaging, `DATASET_TARGET=` for the "Dataset missing" hint; an evaluation
tutorial adds the `RUN_CONFIG` line pointing `flip-job-dir` and `checkpoint` at the checkpoint).
Without it the runner exits `No data mapping` and `test_sim_tutorial_exit_status.py` fails.

Guards that see a Flower tutorial automatically: `test_flower_platform_parity.py` (job type names a
template, required files present, template files byte-identical), `test_flower_min_clients_wiring.py`,
`test_flwr_import_paths.py`, `test_sim_tutorial_exit_status.py`, `test_offline_apps.py`,
`test_tutorial_catalogue.py` (twin query pinned, no direct `SUPERNODE_NAME` read, query shape), ruff.

## 5. Shapes

- **2-D DICOM imaging.** Cohort projects `accession_id`; images via `get_by_accession_number`;
  register the app in `DICOM_APPS` so the orientation suite runs its transform chain (FLIP#821: a
  figure stayed upright while the model trained on transposed images). GPU for the simulator.
- **3-D NIfTI imaging.** XNAT converts the pulled DICOM; labels are not in PACS — they reach the
  project as *enrichment* between the pull and training (`upload-*-labels`; the e2e target's
  `ENRICHMENT_ARGS`, the recorder's `--data-enrichment-cwd/-cmd` + `--publish-segmentations`). The
  app loads `label_*.nii.gz` beside `input_*.nii.gz`; multi-series studies (prostate) select the
  series. Not in `DICOM_APPS`. Each FL client sees only its own `net-N` image slice.
- **Tabular / OMOP-only.** `query.sql` projects `person_id`; `get_dataframe` only; project created
  with imaging **off** (FLIP#1071 — left on, the hub dispatches `create_imaging` and the run dies at
  the image-pull wait): the e2e target pins `--no-imaging`, the demo profile `has_imaging: False`.
  One dev CSV sliced per site; CPU; figure = feature set + label prevalence.
- **Evaluation.** `evaluator.py` + `FlipEvalRecipe` / the `evaluation` template; the checkpoint is
  staged host-side (`download-checkpoints`, `prepare-checkpoint`) into the upload dir, never fetched
  at run time; the e2e target points `QUERY_FILE` at the training tutorial's query. The demo
  recorder's segments 4–5 assume a training job, so do not record one until an evaluation variant
  exists — say so instead.
- **Fine-tune / two-stage.** Fine-tune: `SERVER_CHECKPOINT` seeds round 0, `AGGREGATE_ONLY_REGEX`
  keeps the frozen backbone out of aggregation (Ark+; `prepare-checkpoint` converts the upstream
  pickle host-side under `process_tools/`). Two-stage: `FlipDiffusionRecipe`, `job_type
  diffusion_model`, `validator.py`, perceptual-loss weights via `make weights`; NVFLARE only. Show
  the loop as a Mermaid block in the README. `fed_opt` has a template and no tutorial yet.
- **Weights and checkpoints** follow the root `AGENTS.md` security rule: no run-time download in
  app code, every `torch.load` with `weights_only=True`, uploads `.safetensors` preferred
  (`test_offline_apps.py` is the guard).

## 6. Verify, and what moves when things change

```bash
make -C fl-tutorials test                                            # ruff + both CPU-only suites (CI runs this on every PR touching fl-tutorials/**)
make -C fl-tutorials/nvflare/<category>/<name> export                # NVFLARE: the full job config, no GPU, no data
make -C fl-tutorials download-<dataset>-data
make -C fl-tutorials run-tutorial TUTORIAL=<name>                    # NVFLARE simulator
make -C fl-tutorials run-tutorial TUTORIAL=<name> FL_BACKEND=flower  # Flower simulator
```

The simulator is the only thing that proves the tutorial trains; report honestly which of the
three ran, and check the host (`nvidia-smi`, `ls fl-tutorials/data`) before saying a GPU or a
dataset is missing (the pre-release checklist in `CONTRIBUTING.md` runs `run-all-tutorials` on both
backends). Then the README (`references/readme-shape.md`) and the registrations
(`references/registration-checklist.md`).

When something the tutorials depend on changes:

| Change | Touches |
|--------|---------|
| flip-utils API (`flip.flower.identity`, the recipes, `FlipConstants`, `flip.FLIP`) | every tutorial importing it; `make -C fl-tutorials test` runs in flip-utils' env so the suite sees it |
| an `fl-apps/flower/<job_type>/app/*.py` template | re-copy into every Flower tutorial of that job type (the parity test fails until done) |
| an `fl-apps/nvflare/<template>/required_files.json` | every NVFLARE tutorial of that job type must still carry the set |
| a new guard in `fl-tutorials/tests/` | the whole catalogue at once |
| UI change (`flip-ui/src/**`) | demo videos go stale, curves and figures do not |
| dataset version bump (`trust/.data_version`) or dataset tooling change | reference numbers and figures may change; re-run the figure / curve scripts |
| a release | pre-release checklist: `run-all-tutorials` on both backends, `/audit-tutorial` over the catalogue, re-record what the rows above invalidated |

Porting to the other backend: the file sets differ but the cohort query, model factory, transforms
/ feature engineering, figures and README substance are shared — copy them byte-for-byte and pin
each pair in `scripts/check_tutorial_sync.sh` (NVFLARE copy as the reference), widening the sync
workflow's `paths:` if needed. Changing the dataset or job type: re-run §1 for what changes, then
every registration that named the old value. Known drift to look for when auditing: a Flower client
reading `SUPERNODE_NAME`, a README quoting a target the root Makefile does not forward, a tutorial
missing from the docs catalogue or the recorder's `APPS`, a `torch.load` without `weights_only=True`,
a README with no figure, no Expected performance, no citation. Fix what is small in place; file the rest.
