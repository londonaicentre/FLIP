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

# Data Access API

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![FLIP Data Access API CI](https://github.com/londonaicentre/FLIP/actions/workflows/data_access_api.yml/badge.svg)](https://github.com/londonaicentre/FLIP/actions/workflows/data_access_api.yml)
[![data-access-api](https://ghcr-badge.egpl.dev/londonaicentre/data-access-api/latest_tag?trim=major&label=data-access-api)](https://github.com/londonaicentre/FLIP/pkgs/container/data-access-api)
[![Coverage](https://codecov.io/gh/londonaicentre/FLIP/branch/main/graph/badge.svg?flag=data-access-api)](https://codecov.io/gh/londonaicentre/FLIP)

Service to query the OMOP database and return dataframes, statistics. The query is SQL and given by the researcher.

## Run

You'll need to have started the OMOP database and added data (see [../omop-db/README.md](../omop-db/README.md)).

Then, run

```sh
make dev # needs local `uv` installation
```

To run in Docker

```sh
make up
```

## Testing

### Example cohort queries to test with

```sql
SELECT * FROM omop.radiology_occurrence
```

```sql
select p.gender_source_value, p.year_of_birth, r.protocol_source_value, r.manufacturer, r.accession_id from omop.person p inner join omop.radiology_occurrence r on r.person_id = p.person_id where r.radiology_occurrence_id > 200000 and p.gender_source_value = 'M'
```
