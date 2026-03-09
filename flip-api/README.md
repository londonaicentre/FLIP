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

# flip-api

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![FLIP Central Hub API CI](https://github.com/londonaicentre/FLIP/actions/workflows/central_hub_api.yml/badge.svg)](https://github.com/londonaicentre/FLIP/actions/workflows/central_hub_api.yml)
[![flip-api](https://ghcr-badge.egpl.dev/londonaicentre/flip-api/latest_tag?trim=major&label=flip-api)](https://github.com/londonaicentre/FLIP/pkgs/container/flip-api)
[![Coverage](https://codecov.io/gh/londonaicentre/FLIP/branch/main/graph/badge.svg?flag=flip-api)](https://codecov.io/gh/londonaicentre/FLIP)

The **Central Hub API** is the coordination layer for the FLIP platform. It is the primary interface for researchers
and administrators, managing projects, users, federated learning tasks, model retrieval, and cohort queries across
all connected Trust environments.

## Role in the FLIP Platform

The Central Hub API orchestrates the full lifecycle of a federated learning study:

1. **Project management** — create, approve, and track FL projects across participating Trusts
2. **User management** — via AWS Cognito, managing researcher and administrator accounts
3. **Cohort queries** — dispatch OMOP SQL queries to Trust sites and aggregate results
4. **Model coordination** — trigger training jobs, retrieve aggregated models, and store metrics
5. **Audit logging** — record all significant platform events for governance and compliance

It communicates with each Trust's [trust-api](../trust/trust-api/) and stores all state in a PostgreSQL database.

## Deployment

The flip-api is deployed as a Docker container. In the full local stack, it is started via:

```bash
make central-hub   # starts flip-api, the database, and flip-ui
```

or as part of the full platform:

```bash
make up
```

The API is served on the port defined by `FLIP_API_PORT` in [`.env.development.example`](../.env.development.example)
(default: `8000`). Interactive API documentation (Swagger UI) is available at:

```
http://localhost:<FLIP_API_PORT>/docs
```

## Configuration

The flip-api is configured via environment variables. In development these are set in
[`.env.development.example`](../.env.development.example). Key variables include:

| Variable | Description |
| --- | --- |
| `DATABASE_URL` | PostgreSQL connection string |
| `AWS_REGION` | AWS region for Cognito and S3 |
| `COGNITO_USER_POOL_ID` | AWS Cognito User Pool ID |
| `COGNITO_CLIENT_ID` | AWS Cognito App Client ID |
| `ENCRYPTION_KEY` | Key used for encrypting project IDs sent to Trusts |
| `S3_BUCKET_NAME` | S3 bucket for storing models and artefacts |

See [`.env.development.example`](../.env.development.example) for the full list of required variables.

## Further Reading

- [Full FLIP Documentation](https://londonaicentreflip.readthedocs.io/en/latest/)
- [Contributing & Development Guide](CONTRIBUTING.md)
