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

# AGENTS.md — imaging-api

## Scope

Applies to `trust/imaging-api/`.

## Service Overview

FastAPI service for DICOM and neuroimaging retrieval. It receives internal requests from `trust-api`, talks to Orthanc
and XNAT on the trust network, retrieves image data, and returns results.

## Commands

Run from `trust/imaging-api/`:

```bash
make test
make unit_test
uv run pytest <path>
uv run ruff check .
uv run mypy .
```

## Conventions

- This service is internal to the trust network; do not expose it directly.
- Keep Orthanc and XNAT integration boundaries explicit and tested.
- Be careful with large imaging data and generated files; do not commit local data artifacts.
