# Cohort SQL drafting: prototype (FLIP#1556)

A throwaway prototype for [FLIP#1556](https://github.com/londonaicentre/FLIP/issues/1556), kept to
inform the real implementation. It is not wired into flip-api or flip-ui, it is not run in CI, and it
calls the Anthropic API directly. The issue specifies Amazon Bedrock through the hub task role.

It turns a plain-English cohort description into a FLIP cohort query and checks the result before
showing it:

1. **Draft** (`helper.py`): one model call with FLIP's cohort rules, the OMOP CDM 5.4 columns of 15
   tables (parsed from `trust/omop-db/files/OMOPCDM_postgresql_5.4_ddl.sql`), and four real tutorial
   `query.sql` files as examples. Structured output: `sql`, `style` (vocabulary-free or not),
   `explanation`, `assumptions`, `concepts_used`. An empty `sql` means the model declined.
2. **Check** (`validate.py`, run in data-access-api's own venv):
   - the real trust-side `data_access_api.services.cohort.validate_query`;
   - every table, column and literal comparison resolves against the CDM DDL (`omop_schema.py`,
     sqlglot `qualify` + `annotate_types`);
   - the outer SELECT exposes `person_id` or `accession_id`, as snapshot creation requires.
3. **Retry**: a rejection message goes back to the model, at most twice.

## Run it

Needs `ANTHROPIC_API_KEY` and a checkout whose `trust/data-access-api/.venv` exists (`uv sync` there).
`FLIP_REPO` defaults to this checkout.

```bash
cd scripts/cohort_sql_drafting_prototype
uv run --no-project --with anthropic helper.py "All CT scans of women aged over 60."
uv run --no-project --with anthropic helper.py --eval    # 11 cases -> eval_results.json
uv run --no-project --with anthropic helper.py --hard    # 10 CDM-logic cases -> hard_results.json
uv run --no-project --with anthropic python idioms.py    # about 40 SQL idioms vs the validator, no API calls
uv run --no-project --with anthropic python loop_demo.py # a seeded bad draft fixed through the retry loop
```

## What it showed (2026-11-12, `claude-opus-5-5`, about $1.33 in total)

Results are in `results/`.

- **Validity:** 9/9 ordinary cohorts and 10/10 CDM-logic cohorts passed the validator and the schema
  check first time. Both adversarial prompts were stopped: one declined with an explanation, one
  refused by the API.
- **Semantics:** the SQL logic was right in 7 of 10 hard cases (washout with observation-period
  coverage, "two results at least 90 days apart", first occurrence per person, earliest study per
  person, no row multiplication). The misses were all about codes: drug ingredient vs clinical-drug
  concepts without `concept_ancestor`, abdomen via a procedure link FLIP's data does not fill, units
  by text only, a condition-status field Synthea leaves empty.
- **Schema check:** once the prompt lists the exact columns, the model did not invent names. The check
  still catches hand-edited SQL, and drives the retry loop (`loop_demo.py`).
- **Silent widening:** asked for "CT with contrast", the model dropped the filter (no such column),
  said so, and passed. The UI must show the explanation and assumptions prominently.
- **Validator gap:** `make_date` was rejected; fixed by FLIP#1908 / PR #1909. Age here is year-level
  until that merges.

## What the real feature still needs

See the acceptance checklist on FLIP#1556: Bedrock client and fake, an authorised UNSTAGED-only draft
endpoint, the trust validator rules shared with the hub as a pre-check, per-trust code profiles
(vocabulary loaded, SNOMED or ICD-10), a semantic benchmark, the monthly allowance, and the DPIA.
A dry-run subject count before approval would be the most useful addition beyond the issue.
