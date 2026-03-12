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

```bash
uv sync
make dev   # starts the API with live-reload, loading environment variables correctly
```

To run in Docker:

```bash
make up
```

### Running tests

```bash
make test
```

Ensure XNAT is fully configured (run `make xnat-configure` in the `xnat/` directory) and the test data is available
before running integration tests.
