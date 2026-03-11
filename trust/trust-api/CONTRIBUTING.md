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

# Contributing to trust-api

For general contribution guidelines (coding style, testing, pull requests), see the
[root CONTRIBUTING.md](../../CONTRIBUTING.md).

## Local development setup

### Running the service locally

Ensure the data-access-api and imaging-api are running (or the full trust stack):

```bash
cd ../..
make up-trusts
```

To run only the trust-api in development mode with live-reload:

```bash
uv sync
make dev
```

### Running tests

```bash
make test
```

## Example API payloads

These can be used with the Swagger UI at `http://localhost:<TRUST_API_PORT>/docs` or with tools like Postman/curl.

### Post a cohort query

```json
{
  "project_id": "my_project",
  "query_id": "1",
  "query_name": "my_query",
  "query": "SELECT * FROM omop.radiology_occurrence",
  "trust_id": "mock"
}
```

### Create an imaging project

```json
{
  "project_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "trust_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "project_name": "my_project",
  "query": "SELECT * FROM omop.radiology_occurrence",
  "users": [
    {
      "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "email": "user@company.com",
      "is_disabled": false
    }
  ]
}
```

### Get imaging project status

```
project: test
query: SELECT * FROM omop.radiology_occurrence
```
