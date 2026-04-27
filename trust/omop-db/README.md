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

# Trust OMOP database

Postgres database containing OMOP-ified data.

## Set up

We have prepared mock data for each of the 2 dev trusts (Trust_1 and Trust_2) as postgres data volumes on S3. In order to set up the database locally, these data volumes need to be downloaded/extracted. This will be handled automatically when
creating the trust containers, and similarly they will be updated locally when they are updated on S3 (note for devs: this is controlled by .data_version file in this directory).

```sh
make update-omop-data
```

Start the database container using:

```sh
make up-test-omop-trust1
```

This should not run any initialization scripts as the data volume already contains a populated database.

## Further Reading

- [Trust deployment overview](../README.md)
- [Contributing & Development Guide](../../CONTRIBUTING.md)
