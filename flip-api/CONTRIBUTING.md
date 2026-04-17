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

# Contributing to flip-api

For general contribution guidelines (coding style, testing, pull requests), see the
[root CONTRIBUTING.md](../CONTRIBUTING.md).

## Local development setup

### Running the service locally

```bash
uv sync
make up
```

This starts the API (and the PostgreSQL database) via Docker Compose. To start only the database first, use
`make central-hub` from the repo root or `docker compose -f deploy/compose.development.yml up flip-db -d`.

### Running tests

```bash
make test
```

This runs ruff, mypy, and pytest in sequence and produces an HTML coverage report in `htmlcov/`.

To create test projects in various lifecycle stages for manual testing:

```bash
make create_testing_projects
```

To clean up test data:

```bash
make delete_testing_projects
```

## Connecting pgAdmin to the Central Hub database

pgAdmin is included in the local stack for database inspection. If pgAdmin is running on a remote machine, tunnel the
port first:

```bash
ssh -L 5050:localhost:5050 <your-server>
```

1. Open pgAdmin at <http://localhost:5050> and log in. Credentials are set by `PGADMIN_EMAIL` and `PGADMIN_PASSWORD`
   in [`.env.development.example`](../.env.development.example).
2. Click **Register Server** and configure:
   - **General > Name**: `centralhub` (or any label)
   - **Connection > Host**: value of `DB_HOST` from
     [compose.development.yml](../deploy/compose.development.yml) (e.g. `flip-db`)
   - **Connection > Port**: `5432`
   - **Connection > Username**: `POSTGRES_USER` from compose.development.yml
   - **Connection > Password**: `POSTGRES_PASSWORD` from compose.development.yml
   - Toggle **Save password**

To inspect data: right-click a table (e.g. `fl_logs`) → **Scripts** → **SELECT Script**, then execute.
