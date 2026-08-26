# EHR Risk Prediction — FLIP Flower tutorial

Trains a small MLP to predict **type-2-diabetes onset** from OMOP tabular features (person
demographics + pre-diagnosis condition history) on FLIP's Flower backend. It is FLIP's first
**tabular/EHR-only** example: the whole cohort arrives through `flip.get_dataframe` (arbitrary
SQL over each trust's OMOP CDM database) and **no imaging is fetched at all**. CPU is enough.

The NVFLARE twin lives at
[`fl-tutorials/nvflare/tabular_classification/ehr_risk_prediction/`](../../nvflare/tabular_classification/ehr_risk_prediction/)
— see its README for the full feature/label design, dataset provenance and caveats.
`app/feature_engineering.py` and `app/models.py` are byte-identical copies of that tutorial's
files, and `app/server_app.py` + `app/strategy.py` are byte-identical copies of the
`fl-apps/flower/standard` template — all four are drift-checked by
`scripts/check_tutorial_sync.sh`.

## Data

```bash
make -C fl-tutorials download-synthea-data FL_BACKEND=flower
```

Fetches the fully synthetic 1k-person [Synthea-in-OMOP dataset](https://registry.opendata.aws/synthea-omop/)
(anonymous HTTPS, ~5 MB) and derives `data/synthea/dataframe.csv`. The dev compose stack mounts
that one CSV into **both** SuperNodes; each ClientApp slices out its own site partition by
`person_id` modulo (`app/feature_engineering.py::partition_for_client`), so the dev run stays
genuinely federated. In a deployed run each trust's data-access-api already serves a disjoint
cohort and the partitioning is skipped.

## How to run

```bash
make -C fl-tutorials run-tutorial TUTORIAL=ehr_risk_prediction FL_BACKEND=flower
```

Brings up the standalone Flower stack (SuperLink + 2 SuperNodes + fl-api), submits this
tutorial by name, waits for a terminal status, and tears down. Expect a test AUROC around
0.85–0.95 after 3 rounds.

## Differential privacy

`@app.train` runs under `flip_local_dp_mod`: the model update is clipped to `dp-clipping-norm`
and Gaussian noise calibrated to (`dp-epsilon`, `dp-delta`) is added before the reply leaves the
SuperNode. The MLP is all-float parameters, so the whole update is privatised. Set
`dp-enabled = false` in `pyproject.toml`'s `[tool.flwr.app.config]` to run the same app with the
mechanism off.

## Best-model selection

`best-model-metric = "test_auroc"` (in `app/config.toml` / `pyproject.toml`) makes the server
score each round's aggregated model by held-out AUROC and ship `best_FL_global_model.pt`
alongside the final model. AUROC on a single-class split is reported as NaN, which the selector
refuses loudly rather than silently pinning "best" to a degenerate split.

## On the platform

Upload the `app/` files as the model files of a `standard`-job-type model and submit `query.sql`
as the project's cohort query. The cohort never triggers an imaging pull (there is nothing to
pull), so the project's imaging panel will show an empty import — expected for a tabular project.
