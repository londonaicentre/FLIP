# EHR Risk Prediction — FLIP tutorial (Flower)

Federated training of a small **multi-layer perceptron (MLP)** that predicts **type-2-diabetes
(T2DM) onset** from OMOP tabular features — patient demographics plus pre-diagnosis condition and
visit history — on the **Flower** backend (`ClientApp` / `ServerApp`).

This is the Flower twin of the NVFLARE
[`ehr_risk_prediction`](../../nvflare/tabular_classification/ehr_risk_prediction) tutorial, and
FLIP's first **tabular / EHR-only** example. The whole cohort arrives through `flip.get_dataframe`
(arbitrary SQL over each trust's OMOP CDM database); **no imaging is fetched** — no Orthanc, no XNAT,
no PACS pull, no data-enrichment step — and **CPU is enough** to train it. The data model, features,
label, preprocessing and network are identical to the NVFLARE tutorial; see
[its README](../../nvflare/tabular_classification/ehr_risk_prediction/README.md) for the full
treatment of those. This page focuses on the **Flower-specific mechanics** and how to run it.

---

## Layout

```
app/
  client_app.py          # ClientApp: @app.train / @app.evaluate — fetches the cohort, trains, reports
  server_app.py          # ServerApp: FedAvg loop + best-model save + results upload (from the standard template)
  strategy.py            # FedAvgWithClientMetrics (from the standard template)
  task.py                # single-epoch train/validate helpers
  feature_engineering.py # shared byte-identical with the NVFLARE copy
  models.py              # shared byte-identical with the NVFLARE copy — the MLP factory
  config.json            # per-tutorial hyperparameters read at runtime
  config.toml            # Flower run-config overrides applied by fl-api at submit
pyproject.toml           # flwr app definition + run-config defaults (incl. DP knobs)
query.sql                # the OMOP cohort query (identical to the NVFLARE copy)
```

`server_app.py` and `strategy.py` are copied byte-for-byte from the
[`fl-apps/flower/standard`](../../../fl-apps/flower/standard) template (CI enforces this via
`scripts/check_tutorial_sync.sh`), so all the platform telemetry, best-model selection and
`min_clients` wiring come from the shared template — this tutorial only supplies `client_app.py`,
`task.py` and the shared feature/model code.

## Configuration: two files

- **`app/config.json`** — the tutorial's own hyperparameters (`FEATURES`, `LABEL_COLUMN`,
  `LOCAL_ROUNDS`, `LR_START/END`, `VAL_SPLIT`, `TEST_SPLIT`, `BATCH_SIZE`, `SEED`). `client_app.py`
  reads this at runtime. It is the only per-tutorial knob that rides through the FLIP upload flow,
  because the deployed bundle's `pyproject.toml` comes from the base template, not from here.
- **`app/config.toml`** — Flower run-config overrides that fl-api-flower feeds to
  `flwr run --run-config` at submit: `num-server-rounds = 5` and `best-model-metric = "test_auroc"`.

> `flip-utils` is deliberately **not** declared as a dependency in `pyproject.toml` — Flower
> runtime-installs an app's declared deps and prepends them to `sys.path`, so declaring it would
> shadow the in-image `/opt/flip-utils` copy the platform actually runs (FLIP#767). Pick up a
> flip-utils change with `make build-fl FL_BACKEND=flower`.

## The model and cohort

A one-hidden-layer MLP (`Linear(n_features, 32) → ReLU → Dropout(0.2) → Linear(32, 1)`, ~350
parameters) over nine tabular features (demographics + pre-diagnosis SNOMED condition flags + visit
and condition counts), labelled with the first T2DM diagnosis (SNOMED `44054006`). Preprocessing
(median imputation + z-scoring) is fitted on each site's **local training split only**, so no fitted
state crosses a trust boundary. Full details, including the feature table and the honest caveats of
this teaching cohort, are in the
[NVFLARE README](../../nvflare/tabular_classification/ehr_risk_prediction/README.md#features-and-label).

## Metrics and best-model selection

`client_app.py` streams per-epoch `train_loss` / `val_loss` / `val_auroc` / `val_accuracy` during
training and reports `test_loss` / `test_auroc` / `test_accuracy` on the held-out split during the
evaluate phase. `best-model-metric = "test_auroc"` (in `config.toml`) tells the server-side selector
to keep the best aggregated global model by held-out AUROC, saving `best_FL_global_model.pt`
alongside `FL_global_model.pt`. AUROC on a single-class split is NaN and the selector skips such a
round rather than pinning "best" to a degenerate split.

## Expected performance

On the reference Synthea cohort the aggregated global model reaches roughly **0.85–0.92 held-out
AUROC** after the default 5 rounds. The shipped config (hidden 32, 8 local epochs × 5 rounds,
LR 0.02 → 0.001) was tuned for this; a narrower layer or fewer rounds plateaus lower.

## Differential privacy

Training updates are privatised **on the SuperNode**, before the reply leaves the trust. The
`flip_local_dp_mod` mod from [`flip.flower.privacy`](../../../flip-utils/flip/flower/privacy.py)
clips the local update to a fixed L2 norm and adds Gaussian noise scaled to the configured budget. It
is wired in `app/client_app.py` as `@app.train(mods=[flip_local_dp_mod])`, so it covers training
rounds only — `@app.evaluate` is untouched.

| Key                | Default | Meaning |
|--------------------|---------|---------|
| `dp-enabled`       | `true`  | Master switch. `false` makes the mod a pass-through, so DP-on / DP-off runs use an identical app |
| `dp-clipping-norm` | `1.0`   | L2 norm the update is clipped to before noise |
| `dp-sensitivity`   | `1e-4`  | How much one training example can move the update |
| `dp-epsilon`       | `10.0`  | Privacy budget — smaller means more privacy and more noise |
| `dp-delta`         | `1e-5`  | Probability the guarantee fails outright |

Override per run without editing the app: `flwr run . --run-config "dp-enabled=false"`.

> ⚠️ The DP defaults are **demonstration values, chosen utility-first** so the tutorial still
> converges with the mechanism live; they are not a defensible privacy budget (no cross-round
> composition accounting, and noise does not average down with only a handful of trusts). See the
> xray tutorial's DP section for the full discussion.

## Data

Same Synthea-in-OMOP source as the NVFLARE tutorial (AWS Open Data Registry, anonymous HTTPS, ~5 MB,
fetched at run time, never committed).

- **Local run** derives the CSV both SuperNodes mount:
  ```bash
  make -C fl-tutorials download-synthea-data FL_BACKEND=flower
  ```
- **On the platform / dev stack**, load the same data into each trust's OMOP once (the shipped mock
  OMOP has no condition rows):
  ```bash
  make -C trust load-synthea-ehr TRUST_INDEX=1 OMOP_DB_PORT=5434   # GSTT
  make -C trust load-synthea-ehr TRUST_INDEX=2 OMOP_DB_PORT=5436   # KCH
  ```

## How to run

### Local run (standalone Flower stack)

Brings up the standalone Flower compose stack (SuperLink + 2 SuperNodes + fl-api), submits the job,
polls to completion and tears down — no imaging, CPU only:

```bash
make -C fl-tutorials download-synthea-data FL_BACKEND=flower           # once
make -C fl-tutorials run-tutorial TUTORIAL=ehr_risk_prediction FL_BACKEND=flower
```

Both SuperNodes read the same mounted CSV; each `ClientApp` slices out its own `person_id`-modulo
partition, so the run is genuinely federated off one file.

### On the platform (or `make e2e_smoke`)

Upload [`app/`](app) as the model files of a **`standard`**-job-type model and submit
[`query.sql`](query.sql) as the project's cohort query. The cohort triggers no imaging pull. As an
end-to-end smoke against a running stack — **no enrichment flags**:

```bash
make e2e_smoke FL_BACKEND=flower \
  MODEL_FILES_DIR=fl-tutorials/flower/ehr_risk_prediction/app \
  QUERY_FILE=fl-tutorials/flower/ehr_risk_prediction/query.sql
```

(On the dev stack, run `make -C trust load-synthea-ehr` first so the query returns data.)
