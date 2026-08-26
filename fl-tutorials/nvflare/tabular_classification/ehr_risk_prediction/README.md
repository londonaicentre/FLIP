# EHR Risk Prediction — FLIP tutorial

This tutorial trains a small MLP to predict **type-2-diabetes onset** from OMOP tabular features
(person demographics + pre-diagnosis condition history) using the **NVFLARE Client API**
(`nvflare.client`). The job is defined entirely in Python via `FlipFedAvgRecipe` — no hand-written
JSON configs required.

It is FLIP's first **tabular/EHR-only** example: the whole cohort arrives through
`flip.get_dataframe` (arbitrary SQL over each trust's OMOP CDM database) and **no imaging is
fetched at all** — the counterpart to the imaging tutorials, exercising the platform's other data
path. CPU is enough to train it.

## Data

The reference dataset is the fully synthetic 1k-person
[Synthea dataset in OMOP CDM format](https://registry.opendata.aws/synthea-omop/) from the AWS Open
Data Registry (anonymous HTTPS, no credentials, free of privacy restrictions). Fetch and derive the
training CSVs with:

```bash
make -C fl-tutorials download-synthea-data
```

That runs `utils/build_synthea_dataframe.py`, which downloads three OMOP tables (~5 MB), derives
one feature row per person, and writes `data/synthea/dataframe.csv` plus per-site
`site1/`/`site2/` splits (`person_id` modulo — the same convention the mock trusts use). The
builder's feature logic deliberately mirrors `query.sql`, the OMOP SQL a deployed run sends to
each trust: change one and change the other to match.

### Features and label

`app_files/config.json`'s `FEATURES` lists the model inputs (the order defines the input layout;
the model sizes itself from the list length):

- `age` (vs. 2023, the dataset's export year — deterministic), `is_female`
- Pre-diagnosis history flags, matched on SNOMED `condition_source_value` codes:
  `has_prediabetes`, `has_obesity`, `has_severe_obesity`, `has_hypertension`,
  `has_hyperlipidemia`
- `n_prior_conditions` (distinct pre-diagnosis condition codes), `n_prior_visits`

`LABEL_COLUMN` (`label_t2dm`) is 1 for persons with a type-2-diabetes diagnosis (SNOMED
`44054006`). Features count only events **strictly before** each positive person's first
diagnosis, so the label is not leaked into its own features. Two honest caveats a real study
would address: never-diagnosed persons contribute their whole history (positives have a
truncated observation window), and a rigorous design would define a per-person index date and
time-at-risk window (see the OHDSI patient-level-prediction literature). The 1k Synthea-OMOP
export carries no numeric lab/vital values (`value_as_number` is empty throughout), which is
why the features are condition-history flags rather than measurements.

The cohort dataframe also carries `accession_id` (the person id as text): this cohort fetches no
imaging, but the FLIP dev client requires the column, so `query.sql` aliases it. The app ignores
it.

### Preprocessing

`app_files/feature_engineering.py` (shared byte-identical with the Flower tutorial) imputes with
medians and z-scores with mean/std fitted **on the local training split only** — no fitted state
crosses sites, so there is nothing to federate and no train/eval leakage. The training loss is
`BCEWithLogitsLoss` with `pos_weight` from the local class balance (~6% positive).

## The network

A one-hidden-layer MLP (`Linear(n_features, 16) → ReLU → Dropout → Linear(16, 1)`, ~300
parameters) — effectively regularised logistic regression with a small non-linearity. Both
backends exchange plain torch state-dicts, which is why the model is torch rather than
scikit-learn.

## Metrics and best-model selection

Per-epoch `TRAIN_LOSS`, `VAL_LOSS`, `VAL-AUROC` and `VAL-ACCURACY` stream to the platform
analytics; cross-site evaluation reports `TEST_LOSS`/`TEST-AUROC`/`TEST-ACCURACY` on the held-out
split. `BEST_MODEL_METRIC` (`VAL-AUROC`) enables the server-side `IntimeModelSelector`, exactly as
in the imaging tutorials; AUROC on a single-class split is reported as NaN (published as 0.0) so a
degenerate split stays visible rather than silently pinning "best".

## FLIP-specific values

`FLIP_PROJECT_ID` and `FLIP_QUERY` are read from environment variables (set stubs in `.env.app`).
They are NOT passed as CLI flags because the SQL query contains spaces that don't survive argparse
whitespace-splitting. The trainer reads the query from `config_fed_client.json` at runtime via
`load_query()`.

## How to run

### Export (no GPU, no data needed)

Produces a complete NVFLARE job directory under `./fl_job/flip_fedavg/`:

```bash
make export
```

### Local simulation (CPU is enough)

```bash
make -C fl-tutorials download-synthea-data   # once
make -C fl-tutorials run-tutorial TUTORIAL=ehr_risk_prediction
```

Each simulated site resolves its own split via `SITE{N}_DATAFRAME` from `.env.app` (falling back
to slicing the shared `DEV_DATAFRAME` by `person_id` modulo). Expect a validation AUROC around
0.85–0.95 after 3 rounds — prediabetes is a strong predictor in Synthea's disease modules.

### On the platform

Upload `app_files/` as the model files of a `standard`-job-type model and submit `query.sql` as
the project's cohort query. The cohort never triggers an imaging pull (there is nothing to pull),
so the project's imaging panel will show an empty import — expected for a tabular project.

## Swapping the label

`utils/build_synthea_dataframe.py` and `query.sql` pin the label SNOMED code in one constant
each. If you re-derive with a different label (e.g. another condition in the dataset), keep both
in lockstep, and prefer labels with ≥30 positives per site — below that the builder warns that
validation AUROC will be mostly noise.
