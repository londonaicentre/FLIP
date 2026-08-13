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
[![FLIP Trust API CI](https://github.com/londonaicentre/FLIP/actions/workflows/test_trust_trust_api.yml/badge.svg)](https://github.com/londonaicentre/FLIP/actions/workflows/test_trust_trust_api.yml)
[![trust-api](https://ghcr-badge.egpl.dev/londonaicentre/trust-api/latest_tag?trim=major&label=trust-api)](https://github.com/londonaicentre/FLIP/pkgs/container/trust-api)
[![Coverage](https://codecov.io/gh/londonaicentre/FLIP/branch/main/graph/badge.svg?flag=trust-api)](https://codecov.io/gh/londonaicentre/FLIP)

The **trust-api** is the gateway service deployed at each participating healthcare Trust site. It polls the FLIP
Central Hub for tasks and coordinates local operations — cohort queries, model training, and imaging project
management — without exposing patient data externally.

## Role in the FLIP Platform

The trust-api acts as the local orchestrator at each Trust:

1. **Cohort queries** — polls for and executes OMOP SQL queries from the Central Hub, delegates to [data-access-api](../data-access-api/), and returns aggregated statistics
2. **Imaging projects** — creates projects in XNAT via [imaging-api](../imaging-api/) in response to approved FL studies
3. **Service health** — a background collector probes the trust stack every `HEALTH_COLLECT_INTERVAL_SECONDS` (imaging-api + data-access-api `/health`, XNAT via its anonymous `buildInfo` endpoint, the PACS connector transitively via imaging-api's `ping_pacs` DIMSE echo, the OMOP database via a raw TCP connect) and attaches the snapshot to the heartbeat; the hub surfaces it on the Connection Status page's per-container drawer
4. **Audit** — logs all operations locally for governance purposes

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

API documentation (Swagger UI) is available at the port defined by `TRUST_API_PORT` in this
trust's kit file (`trust/.env.<CODE>.<env>`; templates: `trust/.env.GSTT.development.example`,
`trust/.env.KCH.development.example`, default `8020`):

```
http://localhost:<TRUST_API_PORT>/docs
```

## Configuration

Key environment variables. Per-trust values (ports, keys, expected trust id) live in this
trust's kit file (`trust/.env.<CODE>.<env>`); hub-shared values (`AES_KEY_BASE64`,
`CENTRAL_HUB_API_URL`) are synced into the kit's Hub-shared block by
`make register-trust KIT=<CODE>` (live in prod, inherited from the hub `.env.<env>` in dev):

| Variable | Description |
| --- | --- |
| `EXPECTED_TRUST_ID` | Optional self-check. If set, trust-api aborts at startup when the hub-resolved trust id does not match this value |
| `DATA_ACCESS_API_URL` | Internal URL of the data-access-api |
| `IMAGING_API_URL` | Internal URL of the imaging-api |
| `CENTRAL_HUB_API_URL` | URL of the Central Hub API (for task polling) |
| `TRUST_API_KEY` | Per-trust API key for authenticating with the Central Hub. Lives in this trust's kit file (`trust/.env.<CODE>.<env>`), written by `make register-trust KIT=<CODE>` |
| `AES_KEY_BASE64` | Base64-encoded AES-256 key shared with the hub, used to decrypt encrypted task payloads |
| `POLL_INTERVAL_SECONDS` | Polling frequency in seconds (default: 5) |
| `HEALTH_COLLECT_INTERVAL_SECONDS` | How often the health collector probes the trust services (default: 30) |
| `HEALTH_PROBE_DEGRADED_MS` | A successful probe slower than this reports `degraded` (default: 1000) |
| `XNAT_URL` | Internal URL of XNAT for the health probe (default `http://xnat-web:8080`) |
| `PACS_ID` | XNAT DQR PACS id used for the `ping_pacs` deep probe (default: 1) |
| `OMOP_DB_HOST` / `OMOP_DB_PORT` | OMOP PostgreSQL address for the TCP health probe (defaults `omop-db` / 5432) |
| `TRUST_INTERNAL_SERVICE_KEY_HEADER` | Header name for trust-internal service auth (default `X-Trust-Internal-Service-Key`) |
| `TRUST_INTERNAL_SERVICE_KEY` | Per-trust plaintext key. Forwarded outbound on every call to imaging-api and data-access-api so those services can authenticate the caller. Minted by `register_trust` (`make register-trust KIT=<CODE>`) into this trust's kit file (`trust/.env.<CODE>.<env>`). |

## Authentication

trust-api authenticates **outbound** in two directions:

- **To the Central Hub**: every request carries `TRUST_API_KEY` in the `TRUST_API_KEY_HEADER`. The hub validates against the `api_key_hash` column on the trust's row in the `trust` table.
- **To sibling trust services** (imaging-api, data-access-api): every request carries `TRUST_INTERNAL_SERVICE_KEY` in the configured header. Receivers compare with `hmac.compare_digest` against their own copy of the same per-trust key. See the **Trust-internal Service Authentication** section in [`CLAUDE.md`](../../CLAUDE.md) for the threat model.

trust-api itself does **not** receive inbound requests from the hub or any external caller — it only polls outbound — so it does not validate an inbound trust-internal header.

## Scaling Assumptions

The trust-api task poller is designed to run as a **single replica per trust**. The central hub's
task-claim endpoint does not use row-level database locking, so running multiple poller replicas
for the same trust would cause duplicate task execution.

If horizontal scaling is needed, the hub endpoint (`GET /tasks/pending`) must be
updated to use `SELECT ... FOR UPDATE SKIP LOCKED` to ensure each task is claimed by exactly
one replica.

## Testing

Tests are split into `tests/` (unit-level, no real backing services — `tests/routers/`, `tests/services/`, etc.) and `tests/integration/` (real OMOP database via the shared `trust/deploy/compose.test.yml` stack). See [Where does my test go?](../../CONTRIBUTING.md#where-does-my-test-go) in `CONTRIBUTING.md` for the placement rule, and [`trust/README.md`](../README.md#integration-tests-cohort-query-end-to-end) for how the cohort-query end-to-end suite is wired.

```bash
make local_test         # ruff + mypy + unit suite (no Docker required)
make integration_test   # ruff + mypy + cohort-query end-to-end suite (Docker required)
```

## Further Reading

- [Full FLIP Documentation](https://londonaicentreflip.readthedocs.io/en/latest/)
- [Trust deployment overview](../README.md)
- [Contributing & Development Guide](../../CONTRIBUTING.md)
