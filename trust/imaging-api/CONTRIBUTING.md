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

# Contributing to imaging-api

For general contribution guidelines (coding style, testing, pull requests), see the
[root CONTRIBUTING.md](../../CONTRIBUTING.md).

## Local development setup

### Prerequisites

Ensure both XNAT and Orthanc are running before starting the imaging-api:

- [XNAT setup](../xnat/README.md)
- [Orthanc setup](../orthanc/README.md)

### Running the service locally

The containerised `make up` is the local dev flow (there is no separate
`make dev` target):

```bash
uv sync   # populate the local venv for IDE type-checks / running tests
make up
```

To iterate without Docker (host-side reload), run `uvicorn` directly against the
already-running sibling services (XNAT + Orthanc):

```bash
uv run uvicorn imaging_api.main:app --reload --port "${IMAGING_API_PORT:-8001}"
```

### Running tests

```bash
make test
```

Ensure XNAT is fully configured (run `make xnat-configure XNAT_PROJECT=<stack-name>`
in the `xnat/` directory — e.g. `XNAT_PROJECT=xnat1`) and the test data is available
before running integration tests.
