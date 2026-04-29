# CLAUDE.md — trust-api (Trust Gateway)

## Service Overview

FastAPI gateway running on each trust. Polls the Central Hub for tasks (FL training, cohort queries, imaging requests), dispatches to imaging-api or data-access-api, encrypts results with AES_KEY_BASE64, posts back to hub.

## Key Patterns

- Outbound-only communication: trust-api initiates all connections to the Central Hub via `CENTRAL_HUB_API_URL`
- Polling loop checks hub for pending tasks at regular intervals
- Encrypts payloads with `AES_KEY_BASE64` before sending to hub
- Communicates with sibling services (imaging-api, data-access-api) on the trust Docker network

## Commands

```bash
make test        # ruff + mypy + pytest (unit + integration)
make unit_test   # Unit tests only (alias for local_test)
make up/down     # Docker compose start/stop
```
