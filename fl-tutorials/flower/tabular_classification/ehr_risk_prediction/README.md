# Type 2 diabetes risk from health records

Federated training of a small **multi-layer perceptron (MLP)** that predicts **type-2-diabetes
(T2DM) onset** from OMOP tabular features — patient demographics plus pre-diagnosis condition and
visit history — on the **Flower** backend (`ClientApp` / `ServerApp`).

This is the Flower twin of the FLARE
[`ehr_risk_prediction`](../../../nvflare/tabular_classification/ehr_risk_prediction/README.md) tutorial, and
FLIP's first **tabular / EHR-only** example.

- The whole cohort arrives through `flip.get_dataframe` (arbitrary SQL over each trust's OMOP CDM database).
- **No imaging is fetched**: no Orthanc, no XNAT, no PACS pull, no data-enrichment step.
- **CPU is enough** to train it.

The data model, features, label, preprocessing and network are identical to the FLARE tutorial, whose README covers them in full. This page covers the **Flower-specific mechanics** and how to run it.

## At a glance

| Item | Value |
|---|---|
| What you upload | everything in `app/` |
| Job type | standard |
| Imaging | off |
| Cohort query | `query.sql` |
| Dataset | `make -C fl-tutorials download-synthea-data` |
| Simulator | `make -C fl-tutorials run-tutorial TUTORIAL=ehr_risk_prediction FL_BACKEND=flower` (CPU) |

---

## Layout

```text
app/
  client_app.py          # ClientApp: @app.train / @app.evaluate — fetches the cohort, trains, reports
  server_app.py          # ServerApp: FedAvg loop + best-model save + results upload (from the standard template)
  strategy.py            # FedAvgWithClientMetrics (from the standard template)
  task.py                # single-epoch train/validate helpers
  feature_engineering.py # shared byte-identical with the FLARE copy
  models.py              # shared byte-identical with the FLARE copy — the MLP factory
  config.json            # per-tutorial hyperparameters read at runtime
  config.toml            # Flower run-config overrides applied by fl-api on a platform submit
pyproject.toml           # flwr app definition + run-config defaults (incl. DP knobs)
query.sql                # the OMOP cohort query (identical to the FLARE copy)
```

This tutorial only supplies `client_app.py`, `task.py` and the feature/model code it shares with the FLARE twin.

`server_app.py` and `strategy.py` are byte-for-byte copies of the
[`fl-apps/flower/standard`](../../../../fl-apps/flower/standard) template. Platform telemetry, best-model selection and `min_clients` wiring therefore all come from that template. Do not edit these files here.

CI keeps the copies in sync:

- `fl-tutorials/tests/test_flower_platform_parity.py` checks the two template files.
- `scripts/check_tutorial_sync.sh` checks the three files shared with the FLARE twin: `feature_engineering.py`, `models.py` and `query.sql`.

## Configuration: two files

- **`app/config.json`** — the tutorial's own hyperparameters (`FEATURES`, `LABEL_COLUMN`,
  `LOCAL_ROUNDS`, `LR_START/END`, `VAL_SPLIT`, `TEST_SPLIT`, `BATCH_SIZE`, `SEED`). `client_app.py`
  reads this at runtime. It is the only per-tutorial knob that rides through the FLIP upload flow,
  because the deployed bundle's `pyproject.toml` comes from the base template, not from here.
- **`app/config.toml`** — Flower run-config overrides that fl-api-flower feeds to
  `flwr run --run-config` on a platform submit: `num-server-rounds = 5` and `best-model-metric = "test_auroc"`.
  A tutorial run (`run-tutorial`, or `make submit` on `fl-services/flower`) uses `pyproject.toml`'s values instead.

> `flip-utils` is deliberately **not** declared as a dependency in `pyproject.toml`.
> A local `flwr run` installs an app's declared dependencies at run time and puts them first on
> `sys.path`, so declaring it would replace the flip-utils the run should use with a PyPI release
> (FLIP#767). On the platform nothing is installed at run time: the app imports the FL image's copy.
> To pick up a flip-utils change on the compose stacks, run `make build-fl FL_BACKEND=flower`.

## The model and cohort

A one-hidden-layer MLP (`Linear(n_features, 32) → ReLU → Dropout(0.2) → Linear(32, 1)`, ~350
parameters) over nine tabular features (demographics + pre-diagnosis SNOMED condition flags + visit
and condition counts), labelled with the first T2DM diagnosis (SNOMED `44054006`). Preprocessing
(median imputation + z-scoring) is fitted on each site's **local training split only**, so no fitted
state crosses a trust boundary. Full details, including the feature table and the honest caveats of
this teaching cohort, are in the
[FLARE README](../../../nvflare/tabular_classification/ehr_risk_prediction/README.md#features-and-label).

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
`flip_local_dp_mod` mod from [`flip.flower.privacy`](../../../../flip-utils/flip/flower/privacy.py)
clips the local update to a fixed L2 norm and adds Gaussian noise scaled to the configured budget. It
is wired in `app/client_app.py` as `@app.train(mods=[flip_local_dp_mod])`, so it covers training
rounds only — `@app.evaluate` is untouched.

| Key                | Default | Meaning |
|--------------------|---------|---------|
| `dp-enabled`       | `true`  | Master switch. `false` makes the mod a pass-through, so DP-on / DP-off runs use an identical app |
| `dp-clipping-norm` | `3.0`   | L2 norm the update is clipped to before noise; sized to this model's measured update norm (2.9-3.6) |
| `dp-sensitivity`   | `1e-4`  | How much one training example can move the update |
| `dp-epsilon`       | `10.0`  | Privacy budget — smaller means more privacy and more noise |
| `dp-delta`         | `1e-5`  | Probability the guarantee fails outright |

To override a value for one run without editing the app, pass `--run-config` to `sim-tutorial.sh`, the script `make run-tutorial` runs. It forwards everything after the tutorial name to `flwr run`:

```bash
bash fl-tutorials/flower/sim-tutorial.sh ehr_risk_prediction --run-config "dp-enabled=false"
```

> ⚠️ The DP defaults are **demonstration values, not a privacy budget.** `dp-clipping-norm` is
> sized to this model's update so clipping does not throttle training, but the noise comes from
> `dp-sensitivity` alone, which is negligible beside an update of that norm: the configured
> `dp-epsilon` is not the guarantee these runs get until the noise is calibrated to the clipping norm
> (FLIP#1760). There is also no cross-round composition accounting, and noise does not average down
> with only a handful of trusts. See the
> [xray tutorial's DP section](../../image_classification/xray_classification/README.md#differential-privacy)
> for the full discussion.

## Data

Same Synthea-in-OMOP source as the FLARE tutorial (AWS Open Data Registry, anonymous HTTPS, ~5 MB,
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
  A trust on Kubernetes publishes no host port — use the port-forward form in
  [`trust/deploy/helm/README.md`](../../../../trust/deploy/helm/README.md)
  ("Local clusters (kind)"), then restart its data-access-api.

## How to run

### Local run (the flwr simulator)

No SuperLink container, no SuperNodes, no fl-api, no Docker — `flwr` starts a local SuperLink of
its own. No imaging, CPU only. Every site reads the same CSV and each `ClientApp` slices out its own
`person_id`-modulo partition, so the run is genuinely federated off one file:

```bash
make -C fl-tutorials download-synthea-data FL_BACKEND=flower           # once
make -C fl-tutorials run-tutorial TUTORIAL=ehr_risk_prediction FL_BACKEND=flower
```

### On the platform (or `make e2e_smoke_ehr`)

1. On the dev stack, load the Synthea data into each trust's OMOP database (see [Data](#data)).
2. Create the project with **"Includes imaging data" turned off** (`has_imaging=false`). The hub then skips the imaging stage entirely: no XNAT project at the trusts, no PACS pull, and no imaging status on the project page. Turning it **on** would reject this cohort at submission, because an imaging project's cohort query must return an `accession_id` column and this one has none.
3. Submit [`query.sql`](query.sql) as the project's cohort query.
4. Upload [`app/`](app) as the model files of a **`standard`**-job-type model.

To run the same flow as an end-to-end smoke against a running stack, use the `e2e_smoke_ehr` target. It passes `--no-imaging` for you and needs **no enrichment flags**:

```bash
make e2e_smoke_ehr FL_BACKEND=flower
```

(On the dev stack, load the data first, as in step 1 above.)
