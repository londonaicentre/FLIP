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

## Joining as a new trust (dev hub)

Use this flow when you want a trust whose identity (keys, FL kit slot) came from the hub at runtime. The `trust` table on the hub is the sole trust registry — there is no env-slot model. The trust's plaintext keys never live in a hub-side env file; they live only in the trust's gitignored kit file. The Ansible-driven prod flow lives in `docs/source/deploy-flip/deploy-flip-node-on-prem.rst`.

### 1. Register the trust on the hub

A trust is registered on the **running hub** rather than configured via env-file key dicts. Either:

* **`make register-trusts`** (from the repo root) — registers the `TRUST_<n>_*` trusts configured in `.env.development`, or
* the **Add Trust** button on the **Connection status** page — enter the friendly name, code, and region.

Registration (`register_trust` service, `POST /admin/trusts`):

* mints a `TRUST_API_KEY` and `TRUST_INTERNAL_SERVICE_KEY` (random tokens),
* stores only the SHA-256 of the API key on `trust.api_key_hash`,
* claims the next free `fl_kit_slot` row and binds it to the new trust id.

`make register-trusts` is idempotent and writes the resulting kit straight into the per-trust kit file. The Add-Trust UI surfaces the kit (`TRUST_API_KEY`, `TRUST_INTERNAL_SERVICE_KEY`, `FL_KIT_SLOT`, `FL_KIT_SLOT_NUMBER`) in a modal **once** — the hub never stores either plaintext again, so copy it out before closing the modal and hand it to the trust operator over a secure channel.

### 2. The per-trust kit file

Each trust stack reads a per-trust kit file `trust/.env.Trust_1` / `trust/.env.Trust_2` (gitignored; templates `.env.Trust_*.example`), auto-included by `trust/Makefile`. `make register-trusts` writes it. It carries:

```sh
TRUST_API_KEY=<from kit>
TRUST_INTERNAL_SERVICE_KEY=<from kit>
FL_KIT_SLOT=<from kit>
FL_KIT_SLOT_NUMBER=<from kit>
EXPECTED_TRUST_ID=<from kit>
```

plus that trust's host-local ports and data directories. There is no `TRUST_NAME` — the hub identifies the trust by its API key; the optional `EXPECTED_TRUST_ID` lets trust-api self-check the hub-resolved id at startup. For the on-prem `up-local-trust` stack the equivalent file is `trust/.env.<LOCAL_TRUST_NAME>` (auto-included via `LOCAL_TRUST_KIT_FILE`).

### 3. Start the trust against the hub

```sh
make -C trust down-trust KIT=Trust_2   # if a previous Trust_2 stack is running
make -C trust up-trust KIT=Trust_2
```

The trust-api container authenticates with its `TRUST_API_KEY` and posts a heartbeat to `POST /trust/heartbeat` (no name segment — the hub resolves the trust's identity from the API key). The Connection status page flips the row online within ~30s.

### Cleanup / lost kit

The plaintext keys aren't recoverable — only the hash is on disk. If you didn't save the kit, the only options are:

* delete the row (`DELETE FROM trust WHERE name='<name>';` against `flip-db`, after freeing the slot: `UPDATE fl_kit_slot SET assigned_to_trust_id = NULL, assigned_at = NULL WHERE assigned_to_trust_id = '<trust-id>';`) and re-register, or
* re-register with `make register-trusts`, which mints fresh keys and rewrites the kit file — this also rotates the keys.

## OMOP Database

See dedicated README under [omop-db/README.md](omop-db/README.md) for instructions to populate the database.

## XNAT

`make up` (and `make up-trust KIT=<name>`) brings up that trust's XNAT automatically — it is no longer a separate step. See the dedicated README under [xnat/README.md](xnat/README.md) for standalone XNAT management and debugging.

## Integration tests (cohort-query end-to-end)

The `trust-api` and `data-access-api` integration suites run against a throwaway Compose stack — vanilla Postgres seeded from a small OMOP fixture plus a freshly-built `data-access-api`. The stack is defined in [`deploy/compose.test.yml`](deploy/compose.test.yml) and brought up by [Testcontainers](https://testcontainers-python.readthedocs.io/) inside session-scoped pytest fixtures, so a single test invocation is enough — no `make up` first.

```sh
# trust-api: drives ``handle_cohort_query`` end-to-end through trust-api → data-access-api → omop-db
make -C trust-api integration_test

# data-access-api: hits ``/cohort`` endpoints directly against the same stack
make -C data-access-api integration_test
```

The seed data lives in [`trust-api/tests/integration/fixtures/omop_seed.sql`](trust-api/tests/integration/fixtures/omop_seed.sql) and follows the MI-CDM shape — `image_occurrence` joined to `concept` for modality lookups. Counts there match the assertions in `test_cohort_query.py` and `test_cohort_endpoint.py` (16 patients, 24 image occurrences). When adjusting the seed, update both. The trust-api side mocks nothing on the HTTP boundary — the only stub is an in-process HTTP server that catches the trust-api → flip-api callback (B3 is intentionally scoped to exclude the hub leg, see issue #369).

Both Make targets are also wired into CI via dedicated jobs in `test_trust_trust_api.yml` and `test_trust_data_access_api.yml`.
