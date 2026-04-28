# CLAUDE.md — data-access-api (OMOP Queries)

## Service Overview

FastAPI service for querying the OMOP Common Data Model database. Receives cohort query requests from trust-api, translates them to SQL, executes against omop-db, and returns results.

## Key Patterns

- Connects to omop-db (PostgreSQL port 5432) on the trust network
- Only receives internal requests from trust-api (not directly exposed)
- OMOP CDM query translation layer

## Commands

```bash
make test        # ruff + mypy + pytest (unit + integration)
make unit_test   # Unit tests only
```
