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

# Mock trust setup

Deploy components at the trust level:

* Orthanc ([orthanc](orthanc))
* Imaging API ([imaging-api](imaging-api))
* Data Access API ([data-access-api](data-access-api))
* Trust API ([trust-api](trust-api))
* OMOP Database ([omop-db](omop-db))
* XNAT ([xnat](xnat))

See also the dedicated README files under each folder.

## Setup

### Start Orthanc and trust services

Orthanc, Imaging API, Data Access API and Trust API can be started using the Makefile provided at the repository level:

```sh
make up
```

DICOMs can be uploaded to Orthanc at <http://localhost:8042>.

The Trust API polls the Central Hub for tasks. In development, it connects to the hub over HTTP on the internal Docker network.

## OMOP Database

See dedicated README under [omop-db/README.md](omop-db/README.md) for instructions to populate the database.

## Start XNAT

See dedicated README under [xnat/README.md](xnat/README.md).

## Integration tests (cohort-query end-to-end)

The `trust-api` and `data-access-api` integration suites run against a throwaway Compose stack — vanilla Postgres seeded from a small OMOP fixture plus a freshly-built `data-access-api`. The stack is defined in [`compose.test.yml`](compose.test.yml) and brought up by [Testcontainers](https://testcontainers-python.readthedocs.io/) inside session-scoped pytest fixtures, so a single test invocation is enough — no `make up` first.

```sh
# trust-api: drives ``handle_cohort_query`` end-to-end through trust-api → data-access-api → omop-db
make -C trust-api integration_test

# data-access-api: hits ``/cohort`` endpoints directly against the same stack
make -C data-access-api integration_test
```

The seed data lives in [`trust-api/tests/integration/fixtures/omop_seed.sql`](trust-api/tests/integration/fixtures/omop_seed.sql) and follows the MI-CDM shape — `image_occurrence` joined to `concept` for modality lookups. Counts there match the assertions in `test_cohort_query.py` and `test_cohort_endpoint.py` (16 patients, 24 image occurrences). When adjusting the seed, update both. The trust-api side mocks nothing on the HTTP boundary — the only stub is an in-process HTTP server that catches the trust-api → flip-api callback (B3 is intentionally scoped to exclude the hub leg, see issue #369).

Both Make targets are also wired into CI via dedicated jobs in `test_trust_trust_api.yml` and `test_trust_data_access_api.yml`.
