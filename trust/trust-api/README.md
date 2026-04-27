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

# trust-api

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![FLIP Trust API CI](https://github.com/londonaicentre/FLIP/actions/workflows/trust_api.yml/badge.svg)](https://github.com/londonaicentre/FLIP/actions/workflows/trust_api.yml)
[![trust-api](https://ghcr-badge.egpl.dev/londonaicentre/trust-api/latest_tag?trim=major&label=trust-api)](https://github.com/londonaicentre/FLIP/pkgs/container/trust-api)
[![Coverage](https://codecov.io/gh/londonaicentre/FLIP/branch/main/graph/badge.svg?flag=trust-api)](https://codecov.io/gh/londonaicentre/FLIP)

The **trust-api** is the gateway service deployed at each participating healthcare Trust site. It polls the FLIP
Central Hub for tasks and coordinates local operations — cohort queries, model training, and imaging project
management — without exposing patient data externally.

## Role in the FLIP Platform

The trust-api acts as the local orchestrator at each Trust:

1. **Cohort queries** — polls for and executes OMOP SQL queries from the Central Hub, delegates to [data-access-api](../data-access-api/), and returns aggregated statistics
2. **Imaging projects** — creates projects in XNAT via [imaging-api](../imaging-api/) in response to approved FL studies
3. **Audit** — logs all operations locally for governance purposes

The trust-api polls the [flip-api](../../flip-api/) (Central Hub) for tasks. It does not accept inbound requests from the hub or expose an external user interface.

## Deployment

The trust-api is deployed as part of the Trust-side stack. In the local test environment it starts as part of:

```bash
make up-trusts   # trust services for both mock Trusts
```

or the full stack:

```bash
make up
```

API documentation (Swagger UI) is available at the port defined by `TRUST_API_PORT` in
[`.env.development.example`](../../.env.development.example):

```
http://localhost:<TRUST_API_PORT>/docs
```

## Configuration

Key environment variables (set in [`.env.development.example`](../../.env.development.example)):

| Variable | Description |
| --- | --- |
| `TRUST_NAME` | Name of this Trust instance (must match `Trust.name` in hub DB, e.g. `Trust_1`) |
| `DATA_ACCESS_API_URL` | Internal URL of the data-access-api |
| `IMAGING_API_URL` | Internal URL of the imaging-api |
| `CENTRAL_HUB_API_URL` | URL of the Central Hub API (for task polling) |
| `TRUST_API_KEY` | Per-trust API key for authenticating with the Central Hub. Keys are stored in `TRUST_API_KEYS` JSON dict; generate with `make generate-trust-api-keys` |
| `AES_KEY_BASE64` | Base64-encoded AES-256 key shared with the hub, used to decrypt encrypted task payloads |
| `POLL_INTERVAL_SECONDS` | Polling frequency in seconds (default: 5) |

## Scaling Assumptions

The trust-api task poller is designed to run as a **single replica per trust**. The central hub's
task-claim endpoint does not use row-level database locking, so running multiple poller replicas
for the same trust would cause duplicate task execution.

If horizontal scaling is needed, the hub endpoint (`GET /tasks/{trust_name}/pending`) must be
updated to use `SELECT ... FOR UPDATE SKIP LOCKED` to ensure each task is claimed by exactly
one replica.

## Further Reading

- [Full FLIP Documentation](https://londonaicentreflip.readthedocs.io/en/latest/)
- [Trust deployment overview](../README.md)
- [Contributing & Development Guide](../../CONTRIBUTING.md)
