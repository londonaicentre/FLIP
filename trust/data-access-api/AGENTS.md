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

# AGENTS.md — data-access-api

## Scope

Applies to `trust/data-access-api/`.

## Service Overview

FastAPI service that receives cohort query requests from `trust-api`, translates them for the OMOP Common Data Model,
executes against `omop-db`, and returns results to `trust-api`.

## Commands

Run from `trust/data-access-api/`:

```bash
make test
make unit_test
uv run pytest <path>
uv run ruff check .
uv run mypy .
```

## Conventions

- This service is internal to the trust network; do not expose it directly.
- Keep OMOP SQL/query translation covered by focused unit tests.
- Treat cohort result data as sensitive; avoid unnecessary logging.
