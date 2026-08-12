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

DICOMs can be uploaded to Orthanc at <http://localhost:8042> — log in with `ORTHANC_USERNAME`/`ORTHANC_PASSWORD` from the trust kit file (`trust/.env.<CODE>.<env>`).

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

plus the trust's identity (`TRUST_NAME` / `TRUST_CODE` / `TRUST_REGION`, read by `register-trust`) and its host-local ports and data directories. The optional `EXPECTED_TRUST_ID` lets trust-api self-check the hub-resolved id at startup.

The kit also carries the trust's **disclosure floor**:

```sh
COHORT_QUERY_THRESHOLD=10
```

This is the minimum cohort size the trust will release anything about. Cohort statistics below it are privacy-suppressed (a genuine zero and a small count are indistinguishable), and both row-level routes refuse outright — `/cohort/dataframe`, which supplies FL training data, and `/cohort/accession-ids`, which decides whose imaging is pulled into XNAT. Raise it to release less. It is the operator's setting, not the hub's: trusts need not agree on a value, and the hub cannot lower it. See [`data-access-api/README.md`](data-access-api/README.md#row-level-data-and-the-disclosure-threshold). The same schema serves dev trusts (GSTT/KCH against a local hub), on-prem trusts (against a prod hub), and laptop-against-prod testing — operator picks the kit code (`trust/.env.<CODE>.<env>`) and `make -C trust up-trust KIT=<CODE> PROD=<env>` handles the rest.

### 3. Start the trust against the hub

```sh
make -C trust down-trust KIT=GSTT   # if a previous GSTT stack is running
make -C trust up-trust KIT=GSTT
```

On an on-prem host provisioned by the local playbook, prefix these with `sudo -E` — the login
user is deliberately not in the docker group (see
[deploy/providers/local/README.md](../deploy/providers/local/README.md)). Dev workstations are
unaffected.

The trust-api container authenticates with its `TRUST_API_KEY` and posts a heartbeat to `POST /trust/heartbeat` (no name segment — the hub resolves the trust's identity from the API key). The Connection status page flips the row online within ~30s.

### Cleanup / lost kit

The plaintext keys aren't recoverable — only the hash is on disk. If you didn't save the kit, the only options are:

* delete the row (`DELETE FROM trust WHERE name='<name>';` against `flip-db`, after freeing the slot: `UPDATE fl_kit_slot SET assigned_to_trust_id = NULL, assigned_at = NULL WHERE assigned_to_trust_id = '<trust-id>';`) and re-register, or
* re-register with `make register-trust KIT=<CODE>`, which mints fresh keys and rewrites the kit file — this also rotates the keys.

## OMOP Database

See dedicated README under [omop-db/README.md](omop-db/README.md) for instructions to populate the database.

## XNAT

`make up` (and `make up-trust KIT=<name>`) brings up that trust's XNAT automatically — it is no longer a separate step. See the dedicated README under [xnat/README.md](xnat/README.md) for standalone XNAT management and debugging.

## MONAI Label (optional)

MONAI Label adds AI-assisted annotation to the XNAT OHIF viewer: a **MONAI Label** menu in the
viewer's Masks panel that runs a segmentation model over the scan on screen and lets a user
correct the result interactively.

> **Prerequisite: the XNAT OHIF Viewer plugin, which FLIP does not install by default** — it
> drives the bulk-import livelock in FLIP#662 (see the plugin table in
> [xnat/README.md](xnat/README.md)). Enabling MONAI Label means accepting it back onto that
> trust's XNAT.

It is **off by default** — it needs an NVIDIA GPU on the trust host and pulls a large image, so
a trust that does not want it is unaffected. Enable it per trust:

```sh
make up-trust KIT=<CODE> MONAI_LABEL=true       # or set MONAI_LABEL=true in trust/.env.<CODE>.<env>
```

The trust's kit file carries the rest of the settings:

| Variable | Default | Notes |
| --- | --- | --- |
| `MONAI_LABEL` | `false` | Master switch. Requires `NUM_AVAILABLE_GPUS>0`. |
| `MONAI_LABEL_PORT` | `8030` | Host port the server listens on. |
| `MONAI_LABEL_MODELS` | `deepedit` | Comma-separated radiology models. `all` loads nine, each downloading its own weights. |
| `MONAI_LABEL_PROJECTS` | *(empty)* | XNAT projects the server may read. Empty means **every** project on this trust. |
| `MONAI_LABEL_PUBLIC_URL` | `http://localhost:$MONAI_LABEL_PORT` | See below — this one matters. |
| `MONAI_LABEL_SHM_SIZE` | `8gb` | Shared memory for dataloader workers. |

**`MONAI_LABEL_PUBLIC_URL` is the setting people get wrong.** XNAT stores this URL and hands it
to the OHIF viewer, which calls it **from the clinician's browser** — XNAT never proxies the
request. So it must resolve on the clinician's machine: a Docker service name, or `0.0.0.0`,
will not work. The default only works when the browser runs on the trust host itself. Two
further consequences on a real trust:

- If XNAT is served over **HTTPS**, the browser blocks a plain-`http://` MONAI Label URL as
  mixed content. Terminate TLS in front of MONAI Label, or serve it through the XNAT nginx so
  it is same-origin.
- The MONAI Label API is **unauthenticated** and holds this trust's XNAT service-account
  credentials. Restrict who can reach `MONAI_LABEL_PORT`; do not expose it beyond the
  clinical network.

**Each user must switch the panel on themselves**, once per browser: in the viewer, *Options →
Preferences → Experimental*, tick **MONAILabel Tools**. A **MONAI Label** entry then appears in
the Masks panel. Registering the server is automatic, but this flag is not, and it cannot be
defaulted from the server — the viewer keeps it in the browser's `localStorage`. Until it is
ticked, a perfectly working server is simply absent from the viewer; that is the first thing to
check when it "isn't showing up", followed by `GET /xapi/ohifaiaa/servers` returning the URL and
that URL opening in the browser.

Notes:

- **SAM is always on**, independently of `MONAI_LABEL_MODELS`: the radiology app registers
  `sam_2d` and `sam_3d` (interactive click-to-segment) whenever the `sam2` package is
  importable. Their checkpoint is a further ~900 MB fetched from HuggingFace on first start,
  into the same persisted model directory. Disable with `--conf sam2 false` if not wanted.
- The server reads DICOM straight off this trust's XNAT archive (mounted read-only), falling
  back to HTTP downloads only for scans it cannot resolve there.
- Pretrained weights are fetched on first start and persisted in the `monailabel-models`
  volume, so a container recreate does not re-download them.
- Currently **development/hybrid only**: the production stack runs published images and no
  `monailabel` image is published yet. `MONAI_LABEL=true` with `PROD=true|stag` fails with a
  message pointing here (see FLIP#55).

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
   - On-prem trust: `sudo -E env PROD=true make -C trust up-trust KIT=<CODE>`
     (sudo: the provisioned login user is deliberately not in the docker group;
     `-E` keeps `$HOME` so root's docker reuses your GHCR login)
   - Laptop-against-prod: `make -C trust up-trust KIT=<CODE> PROD=true` (no sudo —
     your workstation isn't provisioned by the on-prem playbook)

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
