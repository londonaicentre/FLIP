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

A trust is registered on the **running hub** rather than configured via env-file key dicts. The kit files (`trust/.env.<CODE>.<env>`) ARE the roster. Either:

* **`make register-trusts`** (from the repo root) — registers the shipped dev roster (every `trust/.env.*.development.example`, currently GSTT and KCH; run automatically by `make up`), or
* **`make register-trust KIT=<CODE>`** — registers one trust (after `make new-trust TRUST_CODE=<CODE> TRUST_NAME="..."` scaffolds its kit), or
* the **Add Trust** button on the **Connection status** page — enter the friendly name, code, and region. This registers the trust on the hub; to produce a deliverable kit file use the `make register-trust` flow above.

Registration (`register_trust` service, `POST /admin/trusts`):

* mints a `TRUST_API_KEY` and `TRUST_INTERNAL_SERVICE_KEY` (random tokens),
* stores only the SHA-256 of the API key on `trust.api_key_hash`,
* claims the next free `fl_kit_slot` row and binds it to the new trust id.

`make register-trust` / `register-trusts` are idempotent and write the resulting credentials straight into the per-trust kit file — the hub never stores either plaintext again.

### 2. The per-trust kit file

Each trust stack reads a per-trust kit file `trust/.env.<CODE>.<env>` (e.g. `trust/.env.GSTT.development`, `trust/.env.<CODE>.production`; gitignored; dev templates `trust/.env.<CODE>.development.example` for GSTT/KCH, generic base `trust/.env.example`), auto-included by `trust/Makefile`. `make register-trust KIT=<CODE>` writes the managed blocks. It carries:

```sh
TRUST_API_KEY=<from kit>
TRUST_INTERNAL_SERVICE_KEY=<from kit>
FL_KIT_SLOT=<from kit>
FL_KIT_SLOT_NUMBER=<from kit>
EXPECTED_TRUST_ID=<from kit>
```

plus the trust's identity (`TRUST_NAME` / `TRUST_CODE` / `TRUST_REGION`, read by `register-trust`) and its host-local ports and data directories. The optional `EXPECTED_TRUST_ID` lets trust-api self-check the hub-resolved id at startup. The same schema serves dev trusts (GSTT/KCH against a local hub), on-prem trusts (against a prod hub), and laptop-against-prod testing — operator picks the kit code (`trust/.env.<CODE>.<env>`) and `make -C trust up-trust KIT=<CODE> PROD=<env>` handles the rest.

### 3. Start the trust against the hub

```sh
make -C trust down-trust KIT=GSTT   # if a previous GSTT stack is running
make -C trust up-trust KIT=GSTT
```

The trust-api container authenticates with its `TRUST_API_KEY` and posts a heartbeat to `POST /trust/heartbeat` (no name segment — the hub resolves the trust's identity from the API key). The Connection status page flips the row online within ~30s.

### Cleanup / lost kit

The plaintext keys aren't recoverable — only the hash is on disk. If you didn't save the kit, the only options are:

* delete the row (`DELETE FROM trust WHERE name='<name>';` against `flip-db`, after freeing the slot: `UPDATE fl_kit_slot SET assigned_to_trust_id = NULL, assigned_at = NULL WHERE assigned_to_trust_id = '<trust-id>';`) and re-register, or
* re-register with `make register-trust KIT=<CODE>`, which mints fresh keys and rewrites the kit file — this also rotates the keys.

## OMOP Database

See dedicated README under [omop-db/README.md](omop-db/README.md) for instructions to populate the database.

## XNAT

`make up` (and `make up-trust KIT=<name>`) brings up that trust's XNAT automatically — it is no longer a separate step. See the dedicated README under [xnat/README.md](xnat/README.md) for standalone XNAT management and debugging.

## Running standalone (remote trust operator)

If you are operating a trust on a host that does not have the hub's
`.env.<env>` file (e.g. an on-prem deployment or a third-party trust), you
need only your trust's kit file (`trust/.env.<CODE>.<env>`).

### One-time setup

1. The hub admin scaffolds and registers your kit
   (`make new-trust TRUST_CODE=<CODE> TRUST_NAME="..." PROD=true`
   then `make register-trust KIT=<CODE> PROD=true`). The result is a complete
   kit file at `trust/.env.<CODE>.production` containing credentials, the AES
   key, the hub URL, image tags, and a host-local profile.
2. The hub admin transmits the file to you out-of-band (SCP-via-SSM for an
   EC2 trust; encrypted channel for on-prem).
3. Drop it at `trust/.env.<CODE>.production` in your checkout.
4. Fill in the **Trust-local credentials** block (Orthanc / OMOP / XNAT /
   Grafana passwords) — these are your secrets, the hub never sees them.
5. Start the stack:
   - EC2 trust: `make -C trust up-trust-ec2 KIT=<CODE> PROD=true`
   - On-prem trust (or laptop-against-prod): `make -C trust up-trust KIT=<CODE> PROD=true`

   The on-prem path skips the dev-only `update-omop-data` / `update-orthanc-data`
   steps (which pull test fixtures from S3 and need hub AWS credentials) —
   real on-prem operators populate `./omop-db/volumes/<CODE>/db_data` and
   `./orthanc/orthanc-storage-<…>` themselves.

### Refreshing shared values (when the hub admin rotates an AES key etc.)

When the hub admin rotates a shared value (AES key, FL backend, image tag),
they will run `make sync-trust-kit KIT=<CODE> PROD=true` on their side. That
produces an updated kit file with the new Hub-shared block; credentials are
preserved. The updated file is transmitted to you using the same out-of-band
channel. Replace your local copy and restart the stack:

```bash
make -C trust restart-trust KIT=<CODE> PROD=true
```

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
