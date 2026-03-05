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

# Contributing to omop-db

For general contribution guidelines (coding style, testing, pull requests), see the
[root CONTRIBUTING.md](../../CONTRIBUTING.md).

## Local development

### Running tests locally

Ensure the database is running before executing tests:

```bash
cd ../..
make up-trusts   # or just: docker compose up omop-db -d
```

Then in this directory:

```bash
uv sync
uv run ruff check . --fix
uv run pytest --tb=short --disable-warnings --cov=app/ --cov-report=html --cov-report=term-missing
```

Or use the shorthand:

```bash
make test
```

## Connecting pgAdmin to the OMOP database

If pgAdmin is running on a remote machine, tunnel the port first:

```bash
ssh -L 5050:localhost:5050 <your-server>
```

1. Open pgAdmin at <http://localhost:5050> and log in. Credentials are `PGADMIN_EMAIL` and `PGADMIN_PASSWORD` from
   [`.env.development`](../../.env.development).
2. Click **Register Server** and configure:
   - **General > Name**: `trust` (or any label)
   - **Connection > Host**: `omop-db`
   - **Connection > Port**: `5432`
   - **Connection > Username**: `OMOP_POSTGRES_USER` from `.env.development`
   - **Connection > Password**: `OMOP_POSTGRES_PASSWORD` from `.env.development`
   - Toggle **Save password**

To inspect data: right-click a table (e.g. `image_occurrence`) → **Scripts** → **SELECT Script**, then execute.

> Importing CSV data via pgAdmin is possible but not recommended — use the provided scripts instead.

## Developer notes

### Accession ID encryption

The encrypt service of the data-import-api is not currently used to encrypt accession IDs before they are stored in
the OMOP database. This is a known limitation being tracked.

### SQL initialization — service account

The following SQL was added to the initialization script (exported via pgAdmin's Backup function) to ensure the
`serviceaccount` user is created correctly:

```sql
-- Create service account user for OMOP database
-- Replace <password> with the value of OMOP_SERVICE_PASSWORD from .env.development
CREATE USER serviceaccount WITH ENCRYPTED PASSWORD '<password>';

GRANT USAGE ON SCHEMA omop TO serviceaccount;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA omop TO serviceaccount;
```
