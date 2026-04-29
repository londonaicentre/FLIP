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

# AGENTS.md — trust

## Scope

Applies to trust-side services and Compose files under `trust/`.

## Architecture

Trusts run at healthcare institutions. Communication with the Central Hub is outbound: `trust-api` polls the hub and
dispatches local work to `imaging-api`, `data-access-api`, and FL clients.

## Services

- `trust-api/`: gateway, task polling, orchestration, encrypted result posting.
- `imaging-api/`: PACS/XNAT image retrieval.
- `data-access-api/`: OMOP cohort query execution.
- `omop-db/`, `orthanc/`, `xnat/`: local/mock data infrastructure.

## Commands

Run from `trust/`:

```bash
make up
make down
make up-trust-1
make up-trust-2
make up-local-trust
make debug
make debug-trust-api
make debug-imaging-api
make debug-data-access-api
make tests
make build
make create-networks
```

## Conventions

- Keep trust services outbound-only unless the deployment architecture is intentionally changed.
- Use `AES_KEY_BASE64` for trust-to-hub payload encryption.
- Keep per-trust identity and keys environment-driven (`TRUST_NAME`, `TRUST_API_KEY`, `TRUST_API_KEYS`).
- Production trust Compose image tags should normally use `${DOCKER_TAG}`.
- For service-specific Python commands and patterns, read the nearest service `AGENTS.md`.
