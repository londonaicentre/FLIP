# CLAUDE.md — data-access-api (OMOP Queries)

## Service Overview

FastAPI service for querying the OMOP Common Data Model database. Receives cohort query requests from trust-api, translates them to SQL, executes against omop-db, and returns results.

## Key Patterns

- Connects to omop-db (PostgreSQL port 5432) on the trust network
- Receives internal requests from trust-api (all `/cohort` endpoints) and from imaging-api (`/cohort/accession-ids`); not directly exposed
- Every internal caller authenticates with the per-trust `TRUST_INTERNAL_SERVICE_KEY` header (see the root `CLAUDE.md` "Trust-internal Service Authentication" section). `/health` stays unauthenticated
- OMOP CDM query translation layer
- `services/cohort.py::validate_query` is the **authority** on cohort-query safety: one parse-validate-emit pass that returns the query re-emitted from the AST it checked. Pass that return value to the engine, never the caller's raw string. The hub runs its own pre-check, but it is fast-feedback only and deliberately weaker — never relax a rule here on the assumption the hub filtered first, and do not mirror trust-local rules onto the hub. See [`README.md`](README.md#cohort-query-validation)
- Both row-level routes — `/cohort/dataframe` (FL training data) and `/cohort/accession-ids` (the accession list that decides whose imaging is pulled into XNAT) — are gated on `COHORT_QUERY_THRESHOLD` and share one fixed refusal string, so a below-threshold cohort is indistinguishable from an empty one and the refusal cannot act as a row-count oracle. The trust enforces this itself; the hub's staging guard is not relied on
- The gates evaluate the **live** cohort on every call, not the cohort as approved. The cohort query is re-run against OMOP at every stage (the imaging status poll alone re-runs it roughly every 10s while a project page is open), so a project can import cleanly and later start refusing. There is no frozen approved-cohort artefact anywhere in FLIP — see FLIP#857
- `COHORT_QUERY_THRESHOLD` is the trust's own disclosure floor (default 10), set per trust in its kit file. It is a `PositiveInt`: `0` would disable both row-level gates (`len(df) < 0` is never true) *and* the statistics suppression at once, so a non-positive value is rejected at import and the service refuses to start rather than run with no floor. Its default lives in one place — the module-level `DEFAULT_COHORT_QUERY_THRESHOLD`, read by both the field default and the empty-string coercion validator; do not re-inline the literal. Requiring the value to be at least the shipped 10 rather than merely positive is FLIP#870. Any new non-`str` setting needs its own empty-string coercion validator: the service Makefile's `export $(shell sed 's/=.*//' $(KIT_ENV_FILE))` strips values from commented lines too, so a commented-out entry arrives as `""` and pydantic rejects it at import

## Commands

```bash
make test        # ruff + mypy + pytest (unit only; integration runs via make integration_test)
make unit_test   # Unit tests only
```
