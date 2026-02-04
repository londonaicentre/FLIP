<!--
    Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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


## Testing

Example blocks for testing:

Post cohort query

```json
{
  "project_id": "my_project",
  "query_id": "1",
  "query_name": "my_query",
  "query": "SELECT * FROM omop.radiology_occurrence",
  "trust_id": "mock"
}
```

Create imaging project from central hub project

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

Get imaging project status

```
project: test
query: SELECT * FROM omop.radiology_occurrence
```
