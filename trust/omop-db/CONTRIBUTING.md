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

The populate tooling is a small uv project (`src/omop_db_tools/`); its unit
tests need no database:

```bash
uv sync
make local_test    # ruff + mypy + pytest tests/unit
```

Exercising the full build → populate → constraints pipeline against real
databases is described in the [README](README.md) ("Building the image").

## Connecting pgAdmin to the OMOP database

pgAdmin ships as an opt-in profile of the build compose stack
(`docker compose -f compose.yml --env-file .env.build --profile pgadmin up -d`
— the `--env-file` matters: compose does not auto-load `.env.build`). If it is
running on a remote machine, tunnel the port first:

```bash
ssh -L 5050:localhost:5050 <your-server>
```

1. Open pgAdmin at <http://localhost:5050> and log in. Credentials are `PGADMIN_EMAIL` and `PGADMIN_PASSWORD` from
   your `.env.build` (template: [`.env.build.example`](.env.build.example)).
2. Click **Register Server** and configure:
   - **General > Name**: `trust` (or any label)
   - **Connection > Host**: `omop-db-trust1` (or `omop-db-trust2`)
   - **Connection > Port**: `5432`
   - **Connection > Username**: `OMOP_POSTGRES_USER` from `.env.build`
   - **Connection > Password**: `OMOP_POSTGRES_PASSWORD` from `.env.build`
   - Toggle **Save password**

To inspect data: right-click a table (e.g. `image_occurrence`) → **Scripts** → **SELECT Script**, then execute.

> Importing CSV data via pgAdmin is possible but not recommended — use the provided scripts instead.

## Developer notes

### Read-only roles

`files/create_readonly_users.sql` (run at image init via
`create_readonly_users.sh`) creates the `omop_readonly_base` role and the
`data_analyst_reader` login that `data-access-api` uses for cohort queries —
its SELECT-only grants are the database half of that API's SQL-injection
defence-in-depth (see
`trust/data-access-api/data_access_api/services/cohort.py`).

### Rotating the data analyst password

The `data_analyst_reader` password is **not baked into the image**: the init
hook reads `DATA_ACCESS_POSTGRES_PASSWORD` from the container environment at
*first* init and stores it in the database cluster — i.e. it lives in the
pgdata volume (and therefore in the published pgdata tarballs, which carry
whatever value they were initialised with). Two rotation paths:

- **Live database** (no rebuild):
  ``ALTER ROLE data_analyst_reader WITH PASSWORD '<new>';`` then update
  `DATA_ACCESS_POSTGRES_PASSWORD` in the trust kit file so data-access-api
  matches.
- **Fresh volumes**: set the new value in `.env.build` and re-run the build →
  populate pipeline (see the README). If the resulting volumes are published as
  new pgdata tarballs, consuming kit files must be updated to the matching
  password.

### Accession ID encryption

Accession IDs are currently stored unencrypted in the OMOP database (the mock
data uses synthetic `FAK`-prefixed IDs). Encrypting them at rest is a known
limitation.
