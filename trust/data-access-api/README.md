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

# Data Access API

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![FLIP Data Access API CI](https://github.com/londonaicentre/FLIP/actions/workflows/data_access_api.yml/badge.svg)](https://github.com/londonaicentre/FLIP/actions/workflows/data_access_api.yml)
[![data-access-api](https://ghcr-badge.egpl.dev/londonaicentre/data-access-api/latest_tag?trim=major&label=data-access-api)](https://github.com/londonaicentre/FLIP/pkgs/container/data-access-api)
[![Coverage](https://codecov.io/gh/londonaicentre/FLIP/branch/main/graph/badge.svg?flag=data-access-api)](https://codecov.io/gh/londonaicentre/FLIP)

The **data-access-api** executes researcher-supplied SQL queries against the Trust's local OMOP database and returns
aggregated statistics and dataframes. It is an internal Trust-side service called only by the
[trust-api](../trust-api/).

## Role in the FLIP Platform

When a researcher submits a cohort query for a federated learning study, the [trust-api](../trust-api/) delegates
the query execution to the data-access-api. The service:

1. Receives a SQL query (restricted to `SELECT` operations on the OMOP schema)
2. Executes it against the local [OMOP database](../omop-db/)
3. Returns aggregated results (counts, statistics) — **no individual patient records are returned**

This service is not directly accessible from outside the Trust network.

## Deployment

The data-access-api starts as part of the Trust-side stack:

```bash
make up-trusts
```

or the full platform:

```bash
make up
```

It requires the [OMOP database](../omop-db/) to be running and populated with data.

## Configuration

Key environment variables (set in [`.env.development`](../../.env.development)):

| Variable | Description |
| --- | --- |
| `OMOP_DB_URL` | PostgreSQL connection string for the OMOP database |
| `DATA_ACCESS_API_PORT` | Port the service listens on |

## Further Reading

- [OMOP Database setup](../omop-db/README.md)
- [Trust deployment overview](../README.md)
- [Contributing & Development Guide](CONTRIBUTING.md)
