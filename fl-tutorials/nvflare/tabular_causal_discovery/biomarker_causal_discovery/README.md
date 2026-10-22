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

# Biomarker causal discovery — FLIP tutorial (NVFLARE)

Federated **bootstrapped FCI** (Fast Causal Inference) over tabular Alzheimer's-disease biomarkers:
plasma markers, brain volumes, cognition and demographics, one row per person, no imaging. The
output is not a trained model but a **partial ancestral graph** (PAG) with a bootstrap frequency on
every edge — which markers drive which, allowing for unmeasured confounders — learned without any
site's rows leaving it. It is the federated form of a centralised analysis developed on ADNI, and
the first FLIP tutorial whose job trains nothing: the platform's ordinary `standard` (FedAvg) job
carries it, because what the sites average is chosen so that the average *is* the analysis.

![Recovered PAG from the federated run on the ADNI cohort](docs/recovered_pag.png)

*The output: the partial ancestral graph two simulated sites recover from the real ADNI cohort
(560 participants, 280 per site, 50 bootstraps), each edge labelled with its bootstrap frequency.
It is the stable graph of the centralised analysis's Fisher-z preset (its main preset uses the kernel test, KCI). ADNI is access-controlled, so the repository ships
this summary figure and the converter that rebuilds the cohort from your own download, never data.*

## The use case

The centralised analysis (`adni_fci`, Natalia Glazman) runs FCI 200 times on bootstrap resamples
of ~560 ADNI participants (its main preset with the kernel test, KCI; its Fisher-z preset is what the
pooled mode here reproduces exactly), with background knowledge that demographics are exogenous and the
cognitive score is a sink, and reports how often each edge appears. Its stable graph is what a
clinician would hope for: sex drives head size, age drives NfL and hippocampal volume, APOE4 drives
pTau217 and the Aβ42/40 ratio, pTau217 and hippocampal volume drive ADAS-Cog13, and intracranial
and hippocampal volume share a latent cause.

On FLIP each trust holds its own participants. A constraint-based algorithm needs one thing from
the data — conditional-independence (CI) tests — so federating it is a question of where the test
runs and what crosses the wire. This tutorial implements two answers, selectable in `config.json`:

```mermaid
flowchart LR
    subgraph pooled["FEDERATION = pooled_statistics (default, 2 rounds)"]
        direction LR
        A1[site, round 1: one mean vector +<br/>one second-moment matrix of its rows,<br/>weight = its n] -->|FedAvg = size-weighted mean| S1[pooled statistics<br/>= centralised correlation]
        S1 --> B1[every site, round 2: parametric bootstrap<br/>of the pooled correlation, full FCI on each draw,<br/>identical PAGs; statistics zeroed]
    end
    subgraph vote["FEDERATION = layer_voting (FedPC, L+1 rounds)"]
        direction LR
        A2[site: CI tests with conditioning<br/>sets of size ℓ on its own rows,<br/>send 0/1 skeleton + sepset marks<br/>weight = 1] -->|FedAvg = share of sites| S2[vote fraction per edge;<br/>any site's sepset survives]
        S2 -->|"ℓ ← ℓ + 1, threshold at VOTE_RATIO"| A2
        S2 --> B2[last round: every site orients<br/>the voted skeleton with FCI's rules]
    end
```

**`pooled_statistics`** is the pooled-test idea of FedC²SL (Wang et al., 2023) specialised to
Fisher's z, whose test needs nothing but second moments and a sample size. Each site sends **one**
mean vector and **one** second-moment matrix of its (log-transformed, fixed-scaled) variables over
all its rows, weighted by its row count — `p + p²` numbers, 132 for the eleven variables here. The
weighted mean of those is exactly the pooled cohort's statistics, so the correlation matrix every
site gets back in round 2 is the centralised one to machine precision, and FCI on it — the complete
algorithm, possible-D-sep stage and all, run identically at every site — is exactly centralised
Fisher-z FCI on the pooled rows (`tests/test_biomarker_causal_discovery.py` pins this against
causal-learn's own `fci()`). The bootstrap is drawn *after* pooling, from the pooled correlation
(a parametric bootstrap: Wishart draws of the sample covariance under the Gaussian model Fisher's z
already assumes, re-standardised, with a stream every site shares), so no site ever releases more
than that one statistic — see "Privacy" for why a per-resample statistic would be too much. In
round 2 the statistics are zeroed out of what a site returns, so the downloaded model carries only
graphs and row counts. Two rounds, no raw rows.

**`layer_voting`** is FedPC (Huang et al., 2023), whose MATLAB release the design was read from: one
global round per conditioning-set size ℓ. Each site tests every still-adjacent pair against subsets
of size ℓ of the neighbours in the *global* skeleton, on its own rows, and returns a 0/1 adjacency
matrix plus 0/1 separating-set membership with weight one. The plain average is the share of sites
that kept each edge; sites threshold it at `VOTE_RATIO` next round (FedPC's vote) and take the union
of separating sets. The last round orients the voted skeleton with FCI's test-free rules. This mode
runs any local CI test — `CI_TEST: "kci"` for the kernel test the centralised main analysis used —
at the cost of exactness: a vote is not a pooled test, small sites' tests are weak, and FedPC itself
does not orient from that union — its paper (§3.2.2) notes that clients disagree on separating sets,
discards them after skeleton learning and re-derives one per unshielded triple by a highest-p-value
search over client data (FedOrien). Orienting from the union is this mode's own simplification; the
orientation errors it causes on otherwise correct adjacencies are visible in the comparison below.

Bootstrapping differs by mode. In pooled mode the *B* resamples are parametric, drawn after pooling
from the pooled correlation with one stream shared by every site, so a site releases one statistic
however large *B* is (see Privacy). In voting mode bootstrap *b* at site *s* resamples that site's
own rows with the stream `(SEED, s, b)`. In both, all *B* resamples travel at once (the graph arrays
carry a bootstrap axis), so the round count never depends on *B*.

## Data

The cohort is **real ADNI data** — the baseline plasma panel, FreeSurfer volumes, APOE genotype,
ADAS-Cog13 and demographics of the centralised analysis's 560 participants — and you obtain it under
your own ADNI licence. ADNI is access-controlled: every user signs a Data Use Agreement that forbids
redistributing participant-level data in any form, so this repository ships no rows, no per-site
split, no thinned fixture and no synthetic stand-in. What it ships is the **code** that turns an
ADNI download into the cohort, and the summary results of running on it. This is the
data-access-tier rule of the repo's tutorial skill.

### Running on real ADNI data

Four steps; the converter reproduces the cohort behind "Expected performance" exactly (560
participants with the April 2025 plasma release):

1. **Apply for access** at https://adni.loni.usc.edu/data-samples/adni-data/ → *Apply for
   access*. You accept the [Data Use Agreement](https://adni.loni.usc.edu/wp-content/themes/adni_2023/documents/ADNI_Data_Use_Agreement.pdf)
   and submit an online application through the LONI Image and Data Archive (IDA) naming your
   institution and the intended analysis ("federated causal discovery over plasma biomarkers, MRI
   volumes and cognition" is enough). Applicants must be affiliated with a scientific or
   educational institution. The Data and Publications Committee reviews within about two weeks and
   emails the decision; the account then needs an annual update to stay active.

2. **Download five study tables** from the IDA (*Download → Study Data*; each CSV is named
   `<TABLE>_<DDMonYYYY>.csv` after the download date). Put them in one directory outside the
   repository:

   | Table | IDA section | Columns the converter reads |
   |---|---|---|
   | `PTDEMOG` | Subjects → Demographics | `RID`, `PTGENDER`, `PTEDUCAT`, `PTDOB` |
   | `APOERES` | Biospecimen → ApoE Genotyping Results | `RID`, `GENOTYPE` |
   | `ADAS` (all phases) | Assessments → Neuropsychological → ADAS-Cog | `RID`, `VISCODE2`, `TOTAL13` |
   | `UCSFFSX7` | Imaging → UCSF Cross-Sectional FreeSurfer (7.x) | `RID`, `EXAMDATE`, `ST10CV`, `ST29SV`, `ST88SV` |
   | `UPENN_PLASMA_FUJIREBIO_QUANTERIX` | Biospecimen → Biospecimen Results → *UPENN Plasma Biomarkers (Aβ42, Aβ40, pTau217, NfL, GFAP) measured by Fujirebio & Quanterix* | `RID`, `VISCODE2`, `EXAMDATE`, `AB42_F`, `AB40_F`, `pT217_F`, `NfL_Q`, `GFAP_Q` |

3. **Convert** them into the tutorial's twelve-column cohort:

   ```bash
   make -C fl-tutorials convert-adni-cohort ADNI_DIR=~/adni_tables
   ```

   `datasets/ad_biomarkers/make_adni_cohort.py` applies the centralised analysis's cohort rules
   (the earliest FreeSurfer scan with a baseline plasma draw within ±3 months, `-4` and negative
   codes as missing, left + right hippocampus, APOE ε4 count from the genotype, age = scan year −
   birth year) and writes `dataframe.csv`, `site1/`, `site2/` and a counts-only
   `cohort_summary.json` to the gitignored `fl-tutorials/data/ad_biomarkers/`, where `.env.app`
   and the Flower wrapper already look. It prints the participant count, never a value. Pass the
   tables explicitly (`--demographics … --plasma …`) if your file names differ, and
   `OUT=/secure/flip-data/ad_biomarkers` to keep the cohort elsewhere (the target refuses a path
   inside the repository that git does not ignore).

4. **Run** the tutorial on it:

   ```bash
   make -C fl-tutorials run-tutorial TUTORIAL=biomarker_causal_discovery
   ```

   With a cohort kept elsewhere, pass `DEV_DATAFRAME=`, `SITE1_DATAFRAME=` and `SITE2_DATAFRAME=`
   pointing at its three CSVs. Then
   `make -C fl-tutorials/nvflare/tabular_causal_discovery/biomarker_causal_discovery summarise`
   gives the edge-frequency table to compare with "Expected performance". A newer plasma release
   adds participants, so expect the count and the weaker edges to move a little.

**What the agreement requires of you.** Keep the converted cohort out of git and out of any
shared drive the agreement does not cover; only summary results (such as edge frequencies) may be
reported, with the ADNI acknowledgement and your download date (see References). Clause 4 of the
agreement also forbids putting ADNI data into third-party AI tools, which includes pasting rows
or file contents into a coding assistant — run the converter and the simulator yourself, and let
such tools see code only. The author ran this path under their own access; the result is reported
in "Expected performance" as numbers only.

**On the platform.** `query.sql` pivots each person's *baseline* (earliest-dated) OMOP measurements
matched on `measurement_source_value` (`PLASMA_PTAU217`, `ICV_MM3`, …) and the education
observation into one row, deterministically ordered. `tests/datasets/ad_biomarkers/` runs it on SQLite
over OMOP tables written from an invented cohort of the same shape and diffs the two, so the
simulator CSV and a deployed fetch cannot drift. The cohort is **not seeded on the dev trusts** and
never will be from the shared trust-data set: nothing ADNI-derived can be published there. A licence
holder loads their own converted cohort into their **local** dev trusts instead — still their team's
copy under the agreement — and then the platform smoke runs end to end:

```bash
make -C trust load-adni-cohort TRUST_INDEX=1 OMOP_DB_PORT=5434   # GSTT (fl-tutorials/data/ad_biomarkers/dataframe.csv)
make -C trust load-adni-cohort TRUST_INDEX=2 OMOP_DB_PORT=5436   # KCH
make e2e_smoke_causal [FL_BACKEND=flower]                        # create project → cohort → run → download
```

The loader (`trust/omop-db/src/omop_db_tools/adni_cohort.py`) writes each trust's `person_id`-modulo
slice as `person` / `measurement` / `observation` rows, exactly the split the simulator's `site1/` and
`site2/` hold, and reloads idempotently. Run this way on a local Flower dev stack (two trusts of 280,
`has_imaging=false`), the smoke's downloaded `FL_global_model.pt` summarises to the same 16 stable
edges as the simulator run above with identical frequencies: the bootstrap streams are keyed on the
site index, which both paths resolve the same way. The FL images must carry causal-learn: build them from this
tree (`make build-fl`) and run the stack with `DOCKER_FL_REGISTRY= DOCKER_FL_TAG=dev`. There is no
demo-video profile, since a recording would show ADNI-derived values.

## Preprocessing and background knowledge (`config.json`)

| Key | Default | Meaning |
|-----|---------|---------|
| `FEDERATION` | `pooled_statistics` | or `layer_voting` |
| `GLOBAL_ROUNDS` | `2` | pooled mode needs exactly 2; voting needs (largest conditioning set + 2), e.g. 6 for sets up to size 4 |
| `N_BOOTSTRAPS` | `50` | bootstrap resamples (`0` = one unresampled fit): parametric draws of the pooled correlation in pooled mode, row resamples per site in voting mode; the centralised analysis used 200 |
| `ALPHA` | `0.05` | CI-test significance level |
| `CI_TEST` | `fisherz` | voting mode only (`kci`, `fastkci`, `rcit`, …); pooled mode is Fisher-z by construction |
| `VOTE_RATIO` | `0.5` | voting mode: keep an edge if at least this share of sites kept it (≥, as in FedPC's MATLAB release, whose default is 0.3; the paper's text says "more than") |
| `LOG_TRANSFORM` | the four plasma markers | fixed, site-independent log of the right-skewed markers; the centralised analysis applied a data-fitted Yeo-Johnson to every column with skew above 1, and a fitted λ cannot be shared without exchanging it — a departure in both the transform and, possibly, the column set (see Honest caveats) |
| `SCALE` | volumes, age, education, ADAS | fixed divisors so second moments are O(1) — see "Privacy" for why this is load-bearing |
| `KNOWLEDGE.EXOGENOUS` | age, sex, education, APOE4 | nothing may cause these |
| `KNOWLEDGE.SINKS` | ADAS-Cog13 | may cause nothing |
| `KNOWLEDGE.NEVER_CONDITION_ON` | ADAS-Cog13 | dropped from every conditioning set (a downstream collider) |
| `STABLE_EDGE_PROB` | `0.5` | the frequency an edge needs to count as stable in the reported metrics |

APOE4 enters as the allele count (0/1/2) rather than the centralised analysis's two dummies: one
variable keeps the graph simple, and Fisher's z on an ordinal is the usual approximation.

## The "model" and what a run reports

`models.get_model()` returns a parameter-free `nn.Module` whose buffers are the exchanged arrays
(`site_counts, stats_mean, stats_moment, skeleton, sepsets, pag, padding`), sized from
`config.json`. The downloaded `FL_global_model.pt` therefore *is* the result: `summarise_results.py`
decodes it into the edge-frequency table and the figure below (`make summarise RESULTS=<path>`). By
the time it is downloaded the statistics buffers are zero — only `pag` and `site_counts` carry
anything — so the result bundle says nothing about the variables beyond the graphs;
`summarise_results.py` warns when they are not zero (a job stopped or failed after round 1, see Privacy).
Per-round metrics streamed to the hub are `site_rows`, `pooled_rows`, `sites_seen`, `stable_edges`,
`mean_edges_per_pag`, `stable_adjacencies` (and `mean_edges_kept` per voting layer); the final
evaluation reports the same for the aggregated PAGs. There is no best-model selection — "best" has
no meaning for a federated state — so `BEST_MODEL_METRIC` is deliberately absent.

Two platform mechanics decide the wire format, and both are worth knowing before you change it:

- **No site knows how many sites there are**, yet Fisher's z needs the pooled sample size. Each
  site writes its row count into its own slot of `site_counts` (slot = the site's index, from the
  trailing number of `Trust_3` / `site-3`); after size-weighted averaging slot *j* holds `n_j² / N`,
  and `N = (Σ_j √slot_j)²` recovers the total exactly.
- **The standard template's percentile-privacy filter** zeroes the 10 % smallest entries of a
  site's update and clips the rest at ±0.01 per local step. Both are no-ops by construction: every
  array travels scaled by `ENCODING_SCALE`, a zero `padding` array keeps the 10th percentile at
  exactly zero, and `SCALE` keeps the moments small enough. A raw volume in mm³ overshoots by orders
  of magnitude — the first simulator run of this tutorial lost every hippocampus edge to the clip
  before `SCALE` existed — so `assert_within_filter_bounds` now refuses such a payload with an error
  naming the variable rather than letting the filter corrupt it silently.

## Expected performance

Reference run: the shipped config (`pooled_statistics`, 50 parametric bootstraps drawn after pooling, α = 0.05) on the
560-participant ADNI cohort that `convert-adni-cohort` builds from the tables listed above (April
2025 plasma release), two `person_id`-modulo sites of 280, NVFLARE simulator on CPU (16-thread
i9-9900K): **under one minute** end to end, of which FCI on 50 bootstraps is about 10 s per site.
The federated run reports the centralised analysis's stable set — 16 edges at frequency ≥ 0.5 — and
a licence holder following the four steps gets these numbers to the second decimal. The table puts the
three ways of getting the graph side by side on the same cohort: the centralised Fisher-z analysis
(200 whole-cohort bootstraps, APOE4 as two dummies), the `pooled_statistics` mode (the default;
50 parametric bootstraps drawn after pooling) and the FedPC-style `layer_voting` mode
(`GLOBAL_ROUNDS=6`, `NUM_ROUNDS=6`, `VOTE_RATIO=0.5`; 50 site-stratified bootstraps):

| Edge | Centralised | Pooled (FedC²SL) | Voting (FedPC) |
|------|-------------|------------------|----------------|
| sex → ICV | 1.00 | 1.00 | 1.00 |
| age → hippocampus | 1.00 | 1.00 | 0.84 |
| age → NfL | 1.00 | 1.00 | 1.00 |
| APOE4 → pTau217 | 1.00 | 1.00 | 0.94 |
| hippocampus → ADAS | 1.00 | 1.00 | 1.00 |
| pTau217 → ADAS | 1.00 | 1.00 | 1.00 |
| NfL → pTau217 | 0.85 | 0.96 | 0.46 |
| sex → ADAS | 0.98 | 0.82 | 0.52 |
| APOE4 → Aβ42/40 | 0.92 | 0.82 | 0.28 |
| education → ADAS | 0.88 | 0.80 | 0.12 |
| ICV ↔ hippocampus (latent head size) | 0.79 | 0.62 | 0.40 |
| NfL → GFAP | 0.57 | 0.68 | 0.82 |
| pTau217 → hippocampus | 0.69 | 0.72 | 0.00 |
| pTau217 → Aβ42/40 | 0.49 | 0.66 | 0.42 |
| age → GFAP | 0.50 | 0.54 | 0.06 |
| pTau217 → GFAP | 0.31 | 0.60 | 0.42 |
| **edges at ≥ 0.5** | **14** | **16** | **8** |

Pooled statistics track the centralised run on every edge: the differences sit on the weaker edges
and come from the three things that genuinely differ — the bootstrap scheme (parametric Gaussian draws of
the pooled correlation versus resampling the rows), the transform (a fixed log versus a fitted
Yeo-Johnson) and the APOE4 encoding — not from the federation, which the exactness test shows adds
nothing of its own. A newer plasma release or a different site split moves the edges below 0.8 by a
few hundredths.

Voting keeps the six strongest edges and loses most of the rest, exactly the small-local-sample failure
of voting that FedC²SL (§4.2) describes: with 280 rows a site, an edge that one site's own test cannot see at α = 0.05 loses the
vote (education → ADAS 0.12, APOE4 → Aβ42/40 0.28), and the union of separating sets mis-orients
some of what survives — pTau217 and GFAP stay adjacent in every bootstrap but the direction is
decided in only 0.42 of them, and pTau217 → hippocampus is lost outright. That is the concrete reason
pooled statistics is the default; voting exists for the kernel test, which has no sufficient
statistics.

**What a wrong run looks like.** `stable_edges` of 0 after the final round means the orientation
round never happened: `GLOBAL_ROUNDS` (or `--num_rounds`) is too small for the mode (2 for pooled;
largest conditioning set + 2 for voting). A graph missing every edge of one variable, with the rest
intact, is the percentile clip above — check that the variable is in `SCALE` or `LOG_TRANSFORM`
(a run on this release raises instead). In voting mode, frequencies all exactly 0 or 1 with
`N_BOOTSTRAPS > 1` mean the sites' bootstrap streams collapsed: check that the site names end in
distinct numbers (pooled mode's stream has no site term, so it cannot show this).
Voting-mode frequencies that drop as rounds proceed are expected (edges are only ever removed);
a skeleton that empties by round 3 means `VOTE_RATIO` is above the share of sites that can
see the dependence (try 0.3, FedPC's value).

## Privacy

**Who enforces what.** Every floor and refusal in this section lives in the uploaded app:
`federated_fci.py` is researcher code on both backends, and a model under an approved project is
submitted without a further approval step. The floors therefore hold against a researcher's
*config*, not against one who edits the code. What a trust itself enforces is its
`COHORT_QUERY_THRESHOLD` on distinct persons at the data-access-api (over the whole served cohort)
and the approval of the cohort query, which fixes the column set the statistics describe; the hub
operator and the FL server see every site's statistics in the clear whatever the app does (no
secure aggregation, below). Read the rest with that split in mind.

**What leaves a site, how often, and what protects it.** In pooled mode a site releases exactly one
mean vector and one second-moment matrix of its transformed variables (`p + p²` = 132 numbers) plus
its row count (the `site_rows` metric and the aggregation weight are exact counts), once, in
round 1 — the pooled-statistics pattern of DataSHIELD and of secure regression on distributed
databases (Gaye et al. 2014; Karr et al. 2005). A single covariance of a few hundred rows is not
invertible to the rows for the *continuous* columns of the shipped variable set: 11 numbers per
participant against 132 released. That holds for this column set. A query that adds an engineered
column — one person's indicator scaled onto an existing variable — would make a single release that
person's record, and no site-side rule can tell such a column from a real one; the approval of the
cohort query is the control there. The **discrete** columns are different: the moments of sex and
the APOE4 count are exact level counts, and their cross-moments are the sum of every other
variable over each level, so a level held by very few rows would be published as such. A site
therefore refuses to release when any discrete column has a level below `MIN_CELL_COUNT` rows
(5, raisable in config, never lowerable), the small-cell rule of statistical disclosure control.
For the same reason it refuses a column that is constant at the site, any row the config would
drop as incomplete for `VARIABLES`, and a non-positive value in a `LOG_TRANSFORM` column rather
than dropping that row: two jobs differing in one dropped row would differ by exactly that row's
contribution, so the researcher's column choice must not select the rows a statistic describes.
The hub, the FL server and every other site see the released statistics in the clear — FLIP has
no secure aggregation — and nothing is noised.

The final model a researcher downloads holds only the bootstrap graphs and the row counts, because
round 2 consumes the statistics and zeroes them. Three things keep a config from undoing that: a site
refuses a job with `GLOBAL_ROUNDS` of 1, whose result would be the statistics themselves; every key
a later round reads (`ALPHA`, `MAX_DEPTH`, `SEED`, the knowledge block, …) is validated in round 1,
before the release, so a value that would only fail in round 2 never gets that far; and a round that
fails after the release still sends its zeroing payload and fails the job on the site's next task.
**What a researcher can still do is stop the job after round 1.** On NVFLARE the server persists the
global model after every round and uploads it when the job ends, however it ends, so that bundle holds
the pooled statistics; `summarise_results.py` warns loudly when it reads such a model. Closing this
needs the platform — a server-side rule that refuses to persist or upload a state whose statistics
buffers are non-zero, or no per-round persistence for this job shape — and until then a trust should
treat the pooled statistics as something the researcher of an approved project can obtain. On NVFLARE
the researcher-settable `AGGREGATION_WEIGHTS` would further let one site be weighted to dominance,
turning the "pooled" statistics into that site's; an approver should reject a config that sets it.
There is also no per-project release ledger: a researcher can re-run the job under one approval with a
different `LOG_TRANSFORM` and collect the moments of another function of the same rows, one more
equation about them per run (`SCALE` is linear and adds nothing). A trust-side ledger that releases
once per project and variable set would close that; it is platform work.

Voting mode releases 0/1 decisions per edge, bootstrap and layer and no statistics, but on resamples
whose indices are public; its leakage has **not** been analysed, so it runs on the simulator only.
A deployed FL client has no switch for it, and a researcher's `config.json` cannot turn it on.

**Why not one statistic per bootstrap.** An earlier version of this tutorial sent a mean and a
second-moment matrix for *each* of the 50 resamples, with resample indices drawn from a seed in
`config.json`. A reviewer showed that this is a system of 50 known weighted sums of the same rows
with publicly reproducible weights, and recovered a 120-row site's rows exactly from one upload, and
most of a 280-row site's binary columns. The present design — one statistic per site, the bootstrap
drawn after pooling from the pooled correlation — removes that channel; the cost is that the
resamples are parametric (Gaussian, the model Fisher's z assumes anyway) rather than resamples of
the actual rows, and the frequencies differ from the earlier runs by a few hundredths.

**Floors and what is still unprotected.** A site refuses to release its statistics below 30
complete rows of distinct persons (a query with several rows per person does not clear the floor
with few people). That floor is a constant in the module; `MIN_SITE_ROWS` in `config.json` can
raise it and never lower it, and the trust raises it further through its own disclosure floor: the
kit's `COHORT_QUERY_THRESHOLD`, which the trust compose files and the Helm chart export to the FL
client as well as to the data-access-api. A trust whose governance document sets a higher
`min_cohort_size` than that variable should set the variable to the same value: the data-access-api
takes the larger of the two, the FL client sees only the variable. The checks run every round and at
the final evaluation, so a site that refused in round 1 reports nothing later either. Refusals name
neither the count nor, for a small cell, the column, nor any statistic: Flower forwards a client's
exception text to the hub (NVFLARE's client API forwards only a generic failure), and the numbers go
to the site's own log. In pooled mode a researcher's `N_BOOTSTRAPS` and `SEED` change nothing a site
releases; in voting mode they do, which is one reason that mode is gated. What remains unprotected is
the one covariance per site itself: with two sites, each can subtract its own from the pooled one and
read the other's. A formal guarantee would add calibrated noise to that single matrix — the Gaussian
mechanism of Analyze Gauss (Dwork et al. 2014) or the Wishart mechanism (Jiang et al. 2016) — at
little accuracy cost at this dimension. The NVFLARE percentile filter and Flower's per-update
clip-and-noise are not that mechanism, both being calibrated for weight deltas: the payload is
shaped so the former is a no-op (checked against the trust's effective clip *and* percentile, from
its site policy), and the latter is switched off in `config.toml`, with the ClientApp refusing a run
that turns it back on, because clip-and-noise on these arrays corrupts the graph silently. Neither
is a trust-side control for this payload in any case — the filter's clip scales with a weight the
client sets, and the mod with a file the researcher uploads. Secure aggregation, so the hub sees only
the sum, is platform work and only helps with three or more sites.

## How to run

```bash
make -C fl-tutorials convert-adni-cohort ADNI_DIR=~/adni_tables         # once, from your own ADNI download (see Data)
make -C fl-tutorials run-tutorial TUTORIAL=biomarker_causal_discovery  # NVFLARE simulator, CPU, < 1 min
make -C fl-tutorials run-tutorial TUTORIAL=biomarker_causal_discovery NUM_ROUNDS=6   # after setting FEDERATION=layer_voting
make -C fl-tutorials/nvflare/tabular_causal_discovery/biomarker_causal_discovery summarise   # edge table + PAG figure
make -C fl-tutorials/nvflare/tabular_causal_discovery/biomarker_causal_discovery export      # the job folder, no data needed
```

`WORKSPACE=<dir>` moves the simulator workspace (default `/tmp/nvflare/causal`), and `make
summarise RESULTS=<FL_global_model.pt>` reads a model downloaded from the hub. The job runs in the
flip-utils environment with its `full` extra, which includes causal-learn, the same set the FL image
installs.

**On the platform:** upload `app_files/` as a `standard` job with imaging **off**
(`has_imaging=false`; the cohort is tabular), paste `query.sql` as the cohort query, and set
`GLOBAL_ROUNDS` in `config.json` to 2 (a site refuses a one-round pooled job, see Privacy). The result bundle's
`FL_global_model.pt` goes through `summarise_results.py` on your machine.

## Honest caveats

- Cross-sectional, baseline data; the arrows are ancestral claims under FCI's assumptions
  (faithfulness, no selection bias), and a bootstrap frequency is a stability measure, not a
  posterior probability.
- The pooled mode is exact for Fisher's z only — a linear-Gaussian test. The kernel test (KCI) of
  the centralised main analysis has no sufficient statistics, so it is available only in voting
  mode, whose approximations are its own.
- The pooled mode's bootstrap is parametric (drawn from the pooled correlation under the Gaussian
  model), not a resample of the rows; the frequencies are a stability measure under that model.
- In voting mode the sites' stratified bootstrap is not the centralised analysis's whole-cohort
  bootstrap; with very unequal sites the frequencies differ slightly.
- The reference run reproduces the centralised analysis's graph from the same 560 participants,
  so agreement between the two is a check of the federation machinery, not independent evidence
  about Alzheimer's disease.
- `LOG_TRANSFORM` logs the four plasma markers; the centralised run Yeo-Johnson-transformed every
  column whose skew exceeded 1, which its `preprocessing.csv` records. If that set includes a
  column not logged here (ADAS-Cog13, education or a volume), part of the difference on the weaker
  edges is the transform, not the federation.
- The release floors and refusals are app code (see Privacy): they bind a researcher's config, not a
  researcher who edits the code, and a job stopped after round 1 still uploads the pooled statistics
  until the platform refuses to persist them.
- `query.sql` ends in `LIMIT 5000`, as every tutorial query does; a trust cohort larger than that
  would be truncated to its lowest `person_id`s without a warning, and the released statistics
  and `pooled_rows` would describe the truncated set.

## References

- **FCI**: Spirtes, P., Glymour, C., Scheines, R. *Causation, Prediction, and Search*, 2nd ed., MIT
  Press, 2000; Zhang, J. "On the completeness of orientation rules for causal discovery in the
  presence of latent confounders and selection bias", *Artificial Intelligence* 172(16–17), 2008.
  Implementation: causal-learn (Zheng, Y. et al., *JMLR* 25, 2024, https://github.com/py-why/causal-learn).
- **FedPC** (the `layer_voting` mode): Huang, J., Guo, X., Yu, K., Cao, F., Liang, J. "Towards
  Privacy-Aware Causal Structure Learning in Federated Setting", *IEEE Transactions on Big Data*,
  2023. https://arxiv.org/abs/2211.06919 (the MATLAB release's layer-wise `layer_federate` vote is
  what this mode reproduces; FedPC discards separating sets after the skeleton and re-derives them in
  its FedOrien p-value step, which this mode replaces with the union of the sites' sets).
- **FedC²SL** (the `pooled_statistics` idea): Wang, Z., Ma, P., Wang, S. "Towards Practical
  Federated Causal Structure Learning", *ECML PKDD 2023*, LNCS 14170. https://arxiv.org/abs/2306.09433,
  code https://github.com/wangzhaoyu07/FedC2SL. Their federated CI test is for discrete variables
  and travels under secure aggregation with a random-projection encoding; here only the pooled-test
  idea is used, in the Gaussian case where it reduces to one moment matrix per site, with neither
  of those protections — the privacy section says what that leaves exposed. (The paper names its own
  variants FedPC and FedFCI, unrelated to Huang et al.'s FedPC above; FedFCI is the closer analogue
  of this tutorial, which runs FCI.)
- **Pooled sufficient statistics across sites** (the pattern `pooled_statistics` instantiates): Karr,
  A. F., Lin, X., Sanil, A. P., Reiter, J. P. "Secure regression on distributed databases", *Journal
  of Computational and Graphical Statistics* 14(2), 2005; Gaye, A. et al. "DataSHIELD: taking the
  analysis to the data, not the data to the analysis", *International Journal of Epidemiology* 43(6),
  2014.
- **The Fisher-z test** used on the pooled correlation (`CorrelationFisherZ`): Kalisch, M.,
  Bühlmann, P. "Estimating high-dimensional directed acyclic graphs with the PC-algorithm", *JMLR*
  8, 2007 — partial correlations from the correlation matrix, `n − |S| − 3` degrees of freedom.
- **PC-stable**, the order-independent skeleton search each voting layer runs: Colombo, D.,
  Maathuis, M. H. "Order-independent constraint-based causal structure learning", *JMLR* 15, 2014.
- **Bootstrap edge frequencies** as confidence in a learned structure: Friedman, N., Goldszmidt,
  M., Wyner, A. "Data analysis with Bayesian networks: a bootstrap approach", *UAI 1999*. **The
  parametric bootstrap** drawn from the pooled correlation: Efron, B., Tibshirani, R. J. *An
  Introduction to the Bootstrap*, Chapman & Hall, 1993, §6.5 (Wishart sampling of the covariance).
- **Why per-resample statistics were withdrawn**: Dinur, I., Nissim, K. "Revealing information while
  preserving privacy", *PODS 2003* — enough released linear functions of the same rows reconstruct
  them.
- **Formal protection for the released covariance** (follow-up): Dwork, C., Talwar, K., Thakurta,
  A., Zhang, L. "Analyze Gauss: optimal bounds for privacy-preserving principal component analysis",
  *STOC 2014*; Jiang, W., Xie, C., Zhang, Z. "Wishart mechanism for differentially private principal
  components analysis", *AAAI 2016*.
- **Related**: Li, L. et al. "Federated Causal Discovery from Heterogeneous Data", *ICLR 2024*
  (FedCDH, https://github.com/lokali/FedCDH); Hahn, J. et al. "fedCI-IOD", arXiv:2603.05149, 2026
  (federated IOD under latent confounding).
- **The centralised analysis** this tutorial federates: N. Glazman, *Causal discovery on ADNI
  multimodal biomarkers* (`adni_fci`, bootstrapped FCI with KCI/Fisher-z and the background-knowledge
  rules reused here), 2026 — ask the author for the citable reference.
- **Data**: ADNI (https://adni.loni.usc.edu) is used under its Data Use Agreement by licence holders
  only; the repository ships no data, just the converter (`datasets/ad_biomarkers/`). Cite FLIP itself via the
  repository's `CITATION.cff`.

**ADNI acknowledgement.** The real-cohort numbers in "Expected performance" are summary results
(edge frequencies, never participant-level values), which the Data Use Agreement permits. Data used
in their preparation were obtained from the Alzheimer's Disease Neuroimaging Initiative (ADNI)
database (adni.loni.usc.edu). The ADNI was launched in 2003 as a public-private partnership, led by
Principal Investigator Michael W. Weiner, MD; its goal has been to test whether serial MRI, PET,
other biological markers, and clinical and neuropsychological assessment can be combined to measure
the progression of mild cognitive impairment and early Alzheimer's disease. Data collection and
sharing for ADNI is funded by the National Institute on Aging (National Institutes of Health Grant
U19AG024904); the grantee organization is the Northern California Institute for Research and
Education. The investigators within ADNI contributed to the design and implementation of ADNI and/or
provided data but did not participate in this analysis (full list:
https://adni.loni.usc.edu/wp-content/uploads/how_to_apply/ADNI_Acknowledgement_List.pdf).
*Data version:* ADNI study tables (demographics, APOE, ADAS, FreeSurfer volumes) downloaded
16 January 2026; plasma assays from the UPenn Plasma Quanterix release of 25 April 2025 (the
25 March 2026 release is the centralised analysis's `plasma_assays` sensitivity preset, not the
main run). Anyone reproducing these numbers under their own access should check the database for
updated tables first, as the agreement requires.

```bibtex
@article{huang2023fedpc,
  title   = {Towards Privacy-Aware Causal Structure Learning in Federated Setting},
  author  = {Huang, Jianli and Guo, Xianjie and Yu, Kui and Cao, Fuyuan and Liang, Jiye},
  journal = {IEEE Transactions on Big Data},
  year    = {2023},
  doi     = {10.1109/TBDATA.2023.3285477}
}
```
