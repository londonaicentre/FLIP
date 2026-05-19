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

Use this flow when you want a trust whose identity (name, keys, FL kit slot) came from the hub at runtime rather than from `.env.development`. It is the dev equivalent of the prod on-prem walkthrough — it deliberately keeps the trust's plaintext keys out of any hub-side env file. The Ansible-driven prod flow lives in `docs/source/deploy-flip/deploy-flip-node-on-prem.rst`.

### 1. Admin creates the trust on the hub

On the **Connection status** page, click **Add Trust** and enter the friendly name, code, and region. The hub (`POST /admin/trusts`):

* mints a `TRUST_API_KEY` and `TRUST_INTERNAL_SERVICE_KEY` (random 32-byte tokens),
* stores only the SHA-256 of the API key on `trust.api_key_hash`,
* claims the next free `fl_kit_slot` row and binds it to the new trust id.

A modal then surfaces the kit (`TRUST_API_KEY`, `TRUST_INTERNAL_SERVICE_KEY`, `FL_KIT_SLOT`, `FL_KIT_SLOT_NUMBER`) **once**. The hub never stores either plaintext again — copy them out of the modal before closing it, and hand them to the trust operator over a secure channel.

### 2. Operator drops the kit file alongside this Makefile

The Makefile auto-includes a per-host kit file and lets its values override the `TRUST_2_VARS` defaults (`trust/Makefile`, search for `TRUST2_KIT_FILE`). Create `trust/.env.trust2` (gitignored) containing:

```sh
TRUST_NAME=<friendly name from kit>
TRUST_API_KEY=<from kit>
TRUST_INTERNAL_SERVICE_KEY=<from kit>
FL_KIT_SLOT=<from kit>
FL_KIT_SLOT_NUMBER=<from kit>
```

For the on-prem `up-local-trust` stack the equivalent file is `trust/.env.<LOCAL_TRUST_NAME>` (auto-included via `LOCAL_TRUST_KIT_FILE`).

### 3. Start the trust against the hub

```sh
make -C trust down-trust-2   # if a previous trust2 stack is running
make -C trust up-trust-2
```

The trust-api container starts authenticating as the kit's `TRUST_NAME`, posts a heartbeat to `/trust/<name>/heartbeat`, and the Connection status page flips the row online within ~30s.

### Cleanup / lost kit

The plaintext keys aren't recoverable — only the hash is on disk. If you didn't save the kit, the only options are:

* delete the row (`DELETE FROM trust WHERE name='<name>';` against `flip-db`, after freeing the slot: `UPDATE fl_kit_slot SET assigned_to_trust_id = NULL, assigned_at = NULL WHERE assigned_to_trust_id = '<trust-id>';`) and re-add via the UI, or
* rotate locally: `uv run python -m flip_api.scripts.generate_trust_key --trust-name <name>` and `UPDATE trust SET api_key_hash = '<new-hash>' WHERE name = '<name>';`, then put the new plaintext into `.env.trust2`.

## OMOP Database

See dedicated README under [omop-db/README.md](omop-db/README.md) for instructions to populate the database.

## Start XNAT

See dedicated README under [xnat/README.md](xnat/README.md).

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
