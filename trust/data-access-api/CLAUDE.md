# CLAUDE.md — data-access-api (OMOP Queries)

## Service Overview

FastAPI service for querying the OMOP Common Data Model database. Receives cohort query requests from trust-api, translates them to SQL, executes against omop-db, and returns results.

## Key Patterns

- Connects to omop-db (PostgreSQL port 5432) on the trust network
- Receives internal requests from trust-api (all `/cohort` endpoints) and from imaging-api (`/cohort/accession-ids`); not directly exposed
- Every internal caller authenticates with the per-trust `TRUST_INTERNAL_SERVICE_KEY` header (see the root `CLAUDE.md` "Trust-internal Service Authentication" section). `/health` stays unauthenticated
- OMOP CDM query translation layer

## Commands

```bash
make test        # ruff + mypy + pytest (unit only; integration runs via make integration_test)
make unit_test   # Unit tests only
```
