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

# Cohort SQL drafting benchmark (FLIP#1556)

A semantic benchmark for the hub's cohort SQL drafting (Beta). It is **run by hand, never in CI**, and it
calls a real model, so every run costs money. pytest does not collect these files (none is named `test_*.py`).

## What it measures

`dataset.jsonl` holds 48 synthetic cohort descriptions in 14 categories (imaging, conditions, time windows,
first occurrence, exclusions, measurements, drugs, era tables, ambiguous, out of scope and prompt injection),
each with a project shape and a reviewer's rubric: the expected outcome, the codes and columns a draft must
use, forbidden fragments and notes. 15 items are `split: "heldout"`. The descriptions are invented; there is
no patient data in the set.

For each item `bench.py` runs flip-api's own drafting loop (`run_draft`: the same prompt, schema, hub checks
and retry rule) with a model that sends the request `BedrockDraftModel` sends, then records:

- whether the SQL passes the trust's real `validate_query` (`trust_validate.py`, in data-access-api's venv)
  and the hub's checks;
- whether the outcome type (draft, clarification, decline) is one the rubric accepts;
- latency and cost per request, from `usage`.

`judge.py` then grades every response with Claude Opus 5.5 against the rubric (correct, partly or wrong, plus
guessed codes and whether they were flagged), on the Message Batches API at half price. `summarise.py` prints
the tables and writes `results.json`.

**13 items carry `uncertain` notes** (`expected.uncertain`): codes or definitions the reviewer was not sure of.
They need a clinician's review before the numbers on those items are relied on.

## Running it

From this directory, with `uv sync` done in `flip-api/` and `trust/data-access-api/`:

```bash
export ANTHROPIC_API_KEY=...                # the Claude API, for drafting (default provider) and the judge
./run_bench.sh --model claude-opus-5-5 --out results/claude-opus-5-5.json
uv run --no-project --with anthropic python judge.py results/claude-opus-5-5.json
python3 summarise.py results/claude-opus-5-5.json
```

Against Amazon Bedrock, as the hub calls it (bedrock-runtime InvokeModel, AWS credentials from the
environment, region `COHORT_DRAFT_BEDROCK_REGION`), pass the Bedrock model id (the hub default, on demand in eu-west-2):

```bash
./run_bench.sh --provider bedrock --model anthropic.claude-opus-5-5 --out results/bedrock-opus-5-5.json
```

`--split heldout` or `--ids img-01,tab-01` run a subset. Results land under `results/` (gitignored).

**Cost:** about $1–2 per model for a full run of 48 items, judge included, at Claude API list prices. Bedrock
prices differ; a Bedrock run is costed at the Claude API rates as an estimate.

## Results

Filled in by hand after a run; numbers only. Full reports go in the PR.

Claude Sonnet 5.5 at effort `medium` on the Claude API unless the row names another model or effort (Claude Opus
5.5 is the hub's default since the UK-only decision), judged by Claude Opus 5.5. Prompt v1 is two runs pooled
(n = 96); every prompt v2 row is one run (n = 48), judged on the updated rubric (grain and Beta scope), so the v1 and
v2 rows are close, not identical, comparisons.

| Prompt | Trust validator | Outcome type | Correct / partly / wrong | Held-out correct / partly / wrong | Clarifications | Latency p50 / p90 | Cost per request / per draft |
|---|---|---|---|---|---|---|---|
| 2026-11-13.1 | 54/54 | 89/96 (93%) | 71 / 25 / 0 | 18 / 12 / 0 | 32/96 (33%) | 3.7 s / 6.3 s | $0.0053 / $0.0094 |
| 2026-11-13.2 | 31/31 | 47/48 (98%) | 45 / 3 / 0 | 15 / 0 / 0 | 6/48 (13%) | 3.4 s / 4.4 s | $0.0056 / $0.0087 |
| 2026-11-13.2, Sonnet 5.5 effort high | 32/32 | 47/48 (98%) | 46 / 2 / 0 | 15 / 0 / 0 | 6/48 (13%) | 4.2 s / 7.5 s | $0.0072 / $0.0108 |
| 2026-11-13.2, Sonnet 5.5 effort low | 29/29 | 48/48 (100%) | 47 / 1 / 0 | 15 / 0 / 0 | 5/48 (10%) | 3.2 s / 4.0 s | $0.0051 / $0.0085 |
| 2026-11-13.2, Opus 5.5 effort medium | 30/30 | 47/48 (98%) | 44 / 4 / 0 | 13 / 2 / 0 | 6/48 (13%) | 6.0 s / 10.8 s | $0.0168 / $0.0269 |
| 2026-11-13.2, Opus 5.5 effort low | 30/30 | 47/48 (98%) | 44 / 4 / 0 | 14 / 1 / 0 | 5/48 (10%) | 5.1 s / 8.3 s | $0.0135 / $0.0215 |
