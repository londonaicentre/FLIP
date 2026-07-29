# CLAUDE.md — data-access-api (OMOP Queries)

## Service Overview

FastAPI service for querying the OMOP Common Data Model database. Receives cohort query requests from trust-api, translates them to SQL, executes against omop-db, and returns results.

## Key Patterns

- Connects to omop-db (PostgreSQL port 5432) on the trust network
- Receives internal requests from trust-api (all `/cohort` endpoints) and from imaging-api (`/cohort/accession-ids`); not directly exposed
- Every internal caller authenticates with the per-trust `TRUST_INTERNAL_SERVICE_KEY` header (see the root `CLAUDE.md` "Trust-internal Service Authentication" section). `/health` stays unauthenticated
- OMOP CDM query translation layer
- `services/cohort.py::validate_query` is the **authority** on cohort-query safety: one parse-validate-emit pass that returns the query re-emitted from the AST it checked. Pass that return value to the engine, never the caller's raw string. The hub runs its own pre-check, but it is fast-feedback only and deliberately weaker — never relax a rule here on the assumption the hub filtered first, and do not mirror trust-local rules onto the hub. See [`README.md`](README.md#cohort-query-validation)
- `/cohort/dataframe` returns row-level training data to FL code and is gated on `COHORT_QUERY_THRESHOLD`; the below-threshold refusal text is fixed so it cannot act as a row-count oracle

## Commands

```bash
make test        # ruff + mypy + pytest (unit only; integration runs via make integration_test)
make unit_test   # Unit tests only
```
