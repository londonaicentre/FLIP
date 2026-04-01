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

The **trust-api** is the gateway service deployed at each participating healthcare Trust site. It receives requests
from the FLIP Central Hub and coordinates local operations — cohort queries, model training, and imaging project
management — without exposing patient data externally.

## Role in the FLIP Platform

The trust-api acts as the local orchestrator at each Trust:

1. **Cohort queries** — receives OMOP SQL queries from the Central Hub, delegates to [data-access-api](../data-access-api/), and returns aggregated statistics
2. **Imaging projects** — creates projects in XNAT via [imaging-api](../imaging-api/) in response to approved FL studies
3. **Audit** — logs all operations locally for governance purposes

The trust-api is only called by the [flip-api](../../flip-api/) (Central Hub). It does not expose an external user interface.

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
| `CENTRAL_HUB_API_URL` | URL of the Central Hub API (for callbacks) |
| `DATA_ACCESS_API_URL` | Internal URL of the data-access-api |
| `IMAGING_API_URL` | Internal URL of the imaging-api |
| `PRIVATE_API_KEY` | Shared key for service-to-service authentication |
| `PRIVATE_API_KEY_HEADER` | Header name for the private API key |

## Further Reading

- [Full FLIP Documentation](https://londonaicentreflip.readthedocs.io/en/latest/)
- [Trust deployment overview](../README.md)
- [Contributing & Development Guide](CONTRIBUTING.md)
