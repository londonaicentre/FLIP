# EHR Risk Prediction — FLIP tutorial (NVFLARE)

Federated training of a small **multi-layer perceptron (MLP)** that predicts **type-2-diabetes
(T2DM) onset** from OMOP tabular features — patient demographics plus pre-diagnosis condition and
visit history. It runs on the **NVFLARE Client API** (`nvflare.client`): the whole job is defined in
Python via `FlipFedAvgRecipe`, with no hand-written JSON configs.

This is FLIP's first **tabular / EHR-only** example. Where the imaging tutorials pull DICOM into
XNAT, this one fetches its entire cohort through `flip.get_dataframe` — arbitrary SQL over each
trust's OMOP CDM database — and touches **no imaging at all**: no Orthanc, no XNAT, no PACS pull, no
data-enrichment step. It is the counterpart to the imaging tutorials, exercising the platform's
other data path, and **CPU is enough** to train it.

---

## Table of contents

- [The use case](#the-use-case)
- [Data](#data) — where it comes from and how it is fetched
- [Features and label](#features-and-label)
- [Preprocessing](#preprocessing)
- [The model](#the-model)
- [Metrics and best-model selection](#metrics-and-best-model-selection)
- [Expected performance](#expected-performance)
- [How to run](#how-to-run)
- [Honest caveats](#honest-caveats-what-a-rigorous-study-would-add)
- [Swapping the label](#swapping-the-label)

---

## The use case

Predicting who is at risk of developing type-2 diabetes is a canonical electronic-health-record
(EHR) risk-prediction task. The clinically interesting part for FLIP is that **the training signal
never leaves each site**: every trust holds its own patients' OMOP records, computes features and
labels locally, and only model weight-updates are exchanged and averaged. No patient row, and no
fitted preprocessing statistic, crosses a trust boundary.

## Data

The reference dataset is the fully synthetic 1k-person
[Synthea dataset in OMOP CDM format](https://registry.opendata.aws/synthea-omop/) from the **AWS Open
Data Registry** — anonymous HTTPS, no credentials, no privacy restrictions, ~5 MB. Synthea generates
realistic-but-fake patients from disease-progression modules; this export is those patients already
mapped to the OMOP Common Data Model, so it drops straight into the platform's OMOP path.

There are **two** data paths, because there are two ways to run the tutorial:

### 1. Local simulation — a derived CSV

For `make run-tutorial` (the NVFLARE simulator), the cohort is a local CSV:

```bash
make -C fl-tutorials download-synthea-data
```

That runs [`datasets/synthea/build_synthea_dataframe.py`](../../../datasets/synthea/build_synthea_dataframe.py), which downloads
three OMOP tables (`person`, `condition_occurrence`, `visit_occurrence`), derives **one feature row
per person**, and writes `fl-tutorials/data/synthea/dataframe.csv` plus per-site `site1/`/`site2/` splits
(`person_id` modulo — the same convention the mock trusts use). Nothing is committed; the raw tables
are cached under `fl-tutorials/data/synthea/.synthea-raw/`.

### 2. On the platform — loaded into OMOP

For a real deployed run (or a `make e2e_smoke` against the dev stack), each trust's data-access-api
executes [`query.sql`](query.sql) against that trust's OMOP database. The **shipped mock OMOP carries
no `condition_occurrence` rows**, so before running the tutorial on the dev stack you load the same
Synthea data into each trust's OMOP once:

```bash
make -C trust load-synthea-ehr TRUST_INDEX=1 OMOP_DB_PORT=5434   # GSTT
make -C trust load-synthea-ehr TRUST_INDEX=2 OMOP_DB_PORT=5436   # KCH
```

(See [`trust/omop-db/README.md`](../../../../trust/omop-db/README.md) → "The Synthea EHR cohort".)
On a **real** trust this step is unnecessary — real condition data is already there and `query.sql`
runs against it unchanged.

The builder's feature logic deliberately **mirrors `query.sql`** (the SQL a deployed run sends to
each trust). Change one and change the other to match — both pin the label SNOMED code and the
feature codes in one place each, and both scope the cohort to persons with at least one recorded
condition.

## Features and label

[`app_files/config.json`](app_files/config.json)'s `FEATURES` list defines the model inputs — the
order is the input layout, and the model sizes its first layer from the list length:

| Feature | Meaning |
| --- | --- |
| `age` | Age vs. 2023 (the dataset's export year — deterministic) |
| `is_female` | `gender_concept_id == 8532` |
| `has_prediabetes` | SNOMED `15777000`, before first T2DM diagnosis |
| `has_obesity` | SNOMED `162864005` |
| `has_severe_obesity` | SNOMED `408512008` |
| `has_hypertension` | SNOMED `38341003` **or** `59621000` (see below) |
| `has_hyperlipidemia` | SNOMED `55822004` |
| `n_prior_conditions` | Distinct pre-diagnosis condition codes |
| `n_prior_visits` | Pre-diagnosis visit count |

Codes are matched on the raw SNOMED `*_source_value` strings, as Synthea emits them, so the query
needs **no concept/vocabulary tables** and runs on a vocabulary-free trust.

### Why the query reads two tables and accepts two hypertension codes

Two properties of a SNOMED code are decided by whoever built the OMOP dataset rather than by the
clinical record, and getting either wrong costs a feature **silently** — the cohort still returns
the right number of rows, and the column is simply `0` for every person:

- **Which table a code lands in.** The BMI codes (`162864005`, `408512008`) are SNOMED *findings*, so
  a domain-aware ETL such as [OHDSI ETL-Synthea](https://github.com/OHDSI/ETL-Synthea) routes them to
  `OBSERVATION`, whereas the published AWS export puts every code in `CONDITION_OCCURRENCE` with
  `concept_id = 0`. The risk-factor lookups therefore read a `UNION ALL` of both tables.
- **Which code a concept carries.** Essential hypertension is `38341003` in the published 1k export
  and `59621000` from Synthea v3.3.0 onwards, so both are accepted.

`n_prior_conditions` deliberately stays `CONDITION_OCCURRENCE`-only: Synthea emits socioeconomic
findings (employment, housing, social isolation) as observations, and counting those as comorbidities
would change the feature's meaning between datasets.

**Held-out AUROC does not catch this class of breakage** — measured against a regenerated dataset, a
query missing both fixes left three of the nine features dead for every person and still scored
0.941, because Synthea's scripted prediabetes→T2DM progression carries most of the signal.
`fl-tutorials/tests/test_ehr_query_portability.py` therefore asserts the structure directly, and
checks that `query.sql` and the local-simulation builder accept exactly the same codes.

`LABEL_COLUMN` (`label_t2dm`) is 1 for persons with a type-2-diabetes diagnosis (SNOMED `44054006`).
Features count only events **strictly before** each positive person's first diagnosis, so the label
is never leaked into its own features.

## Preprocessing

[`app_files/feature_engineering.py`](app_files/feature_engineering.py) (shared byte-identical with the
Flower tutorial) imputes missing values with medians and z-scores with mean/std fitted **on the local
training split only**. No fitted state crosses sites, so there is nothing to federate and no
train/eval leakage. The training loss is `BCEWithLogitsLoss` with `pos_weight` from the local class
balance (~6 % positive); validation and test use the plain unweighted loss so sites of differing
prevalence stay comparable.

## The model

A one-hidden-layer MLP — `Linear(n_features, 32) → ReLU → Dropout(0.2) → Linear(32, 1)`, ~350
parameters — effectively a small, regularised logistic regression with a non-linearity. Both backends
build it from the same factory and exchange plain torch **state-dicts**, which is why the model is
torch rather than scikit-learn.

## Metrics and best-model selection

Per-epoch `TRAIN_LOSS`, `VAL_LOSS`, `VAL-AUROC` and `VAL-ACCURACY` stream to the platform analytics;
cross-site evaluation reports `TEST_LOSS` / `TEST-AUROC` / `TEST-ACCURACY` on the held-out split.
`BEST_MODEL_METRIC` (`VAL-AUROC`) enables the server-side `IntimeModelSelector`, exactly as in the
imaging tutorials. AUROC on a single-class split is reported as NaN (published as 0.0) so a
degenerate split stays visible rather than silently pinning "best".

## Expected performance

On the reference Synthea cohort, the aggregated global model reaches roughly **0.85–0.92 held-out
AUROC** after the default 5 rounds (measured across seeds; prediabetes is a strong, early predictor
in Synthea's disease modules). Model capacity and training length matter here: the shipped config
(hidden layer of 32, 8 local epochs × 5 global rounds, LR 0.02 → 0.001) was tuned for this — a
narrower 16-unit layer or a single epoch/round plateaus markedly lower.

## How to run

### Export the job config (no GPU, no data needed)

Produces a complete NVFLARE job directory under `./fl_job/flip_fedavg/`:

```bash
make export
```

### Local simulation (CPU is enough)

```bash
make -C fl-tutorials download-synthea-data                          # once
make -C fl-tutorials run-tutorial TUTORIAL=ehr_risk_prediction
```

Each simulated site resolves its own split via `SITE{N}_DATAFRAME` from `.env.app` (falling back to
slicing the shared `DEV_DATAFRAME` by `person_id` modulo), so the run stays genuinely federated even
off a single file. Override the round/client counts inline, e.g.
`make run-tutorial TUTORIAL=ehr_risk_prediction NUM_ROUNDS=10`.

### On the platform (or `make e2e_smoke`)

1. Upload [`app_files/`](app_files) as the model files of a **`standard`**-job-type model.
2. Submit [`query.sql`](query.sql) as the project's cohort query.

The cohort fetches no imaging, so create the project with **"Includes imaging data" turned off**
(`has_imaging=false`): the hub then skips the imaging stage entirely — no XNAT project at the trusts,
no PACS pull — and the project page shows no imaging status. (The `accession_id` column in `query.sql` is kept only until #1130 removes it; the platform no
longer reads it for a project created with imaging off.) On the dev stack, run `make -C trust load-synthea-ehr` (above) first so the query has
data. As an end-to-end smoke against a running stack — the `e2e_smoke_ehr` target passes
`--no-imaging` for you, **no enrichment flags**:

```bash
make e2e_smoke_ehr
```

### FLIP-specific values

`FLIP_PROJECT_ID` and `FLIP_QUERY` are read from environment variables (stubs in `.env.app`), **not**
CLI flags — the SQL query contains spaces that don't survive argparse whitespace-splitting. The
trainer reads the query from `config_fed_client.json` at runtime via `load_query()`.

## Honest caveats (what a rigorous study would add)

This is a teaching example, not a validated clinical model. Two things a real patient-level
prediction study would address:

- **Observation window.** Never-diagnosed persons contribute their whole history, while positives
  have a truncated pre-diagnosis window — a rigorous design would define a per-person **index date**
  and a fixed time-at-risk window (see the OHDSI patient-level-prediction literature).
- **No lab/vital values.** The 1k Synthea-OMOP export carries no numeric measurements
  (`value_as_number` is empty throughout), which is why the features are condition-history flags
  rather than HbA1c/BMI values.

The cohort is scoped (in both `query.sql` and the builder) to persons with at least one recorded
condition — a sensible "has clinical history" inclusion criterion that also cleanly excludes the
dev mock's imaging-only persons.

## Swapping the label

[`datasets/synthea/build_synthea_dataframe.py`](../../../datasets/synthea/build_synthea_dataframe.py) and `query.sql` pin the label
SNOMED code in one constant each. To re-derive with a different label (e.g. another condition in the
dataset), keep both in lockstep, and prefer labels with ≥30 positives per site — below that the
builder warns that validation AUROC will be mostly noise.
