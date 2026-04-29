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

# AGENTS.md — trust-api

## Scope

Applies to `trust/trust-api/`.

## Service Overview

FastAPI gateway running at each trust. It polls the Central Hub, dispatches tasks to sibling services, encrypts results
with `AES_KEY_BASE64`, and posts results back to the hub.

## Commands

Run from `trust/trust-api/`:

```bash
make test
make unit_test
make up
make down
uv run pytest <path>
uv run ruff check .
uv run mypy .
```

## Conventions

- Preserve outbound-only communication to the Central Hub via `CENTRAL_HUB_API_URL`.
- Keep sibling-service calls on the trust Docker network.
- Do not log plaintext secrets, API keys, or decrypted payloads.
