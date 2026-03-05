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

# Contributing to data-access-api

For general contribution guidelines (coding style, testing, pull requests), see the
[root CONTRIBUTING.md](../../CONTRIBUTING.md).

## Local development setup

### Running the service locally

Ensure the OMOP database is running and populated first (see [../omop-db/README.md](../omop-db/README.md)).

```bash
uv sync
make dev   # starts the API with live-reload using local uv installation
```

To run in Docker:

```bash
make up
```

### Running tests

```bash
make test
```

## Example cohort queries for testing

Use these via the Swagger UI at `http://localhost:<DATA_ACCESS_API_PORT>/docs` or with curl/Postman.

```sql
SELECT * FROM omop.radiology_occurrence
```

```sql
SELECT p.gender_source_value, p.year_of_birth, r.protocol_source_value, r.manufacturer, r.accession_id
FROM omop.person p
INNER JOIN omop.radiology_occurrence r ON r.person_id = p.person_id
WHERE r.radiology_occurrence_id > 200000
  AND p.gender_source_value = 'M'
```
