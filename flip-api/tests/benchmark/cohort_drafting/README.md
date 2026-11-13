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
./run_bench.sh --model claude-sonnet-5-5 --out results/claude-sonnet-5-5.json
uv run --no-project --with anthropic python judge.py results/claude-sonnet-5-5.json
python3 summarise.py results/claude-sonnet-5-5.json
```

Against Amazon Bedrock, as the hub calls it (bedrock-runtime InvokeModel, AWS credentials from the
environment, region `COHORT_DRAFT_BEDROCK_REGION`), pass a Bedrock model or inference-profile id:

```bash
./run_bench.sh --provider bedrock --model eu.anthropic.claude-sonnet-5-5 --out results/bedrock-sonnet-5-5.json
```

`--split heldout` or `--ids img-01,tab-01` run a subset. Results land under `results/` (gitignored).

**Cost:** about $1–2 per model for a full run of 48 items, judge included, at Claude API list prices. Bedrock
prices differ; a Bedrock run is costed at the Claude API rates as an estimate.

## Results

Filled in by hand after a run; numbers only. Full reports go in the PR.
