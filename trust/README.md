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
* Observability ([observability](observability)) — Grafana (host `GRAFANA_PORT`, container 3000),
  Loki (host `LOKI_PORT`, container 3100) and an Alloy log collector (internal only, no published
  port). Each trust runs its own; logs never leave the trust.

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

The kit also carries **XNAT's own AE title** and the **upstream PACS** block (see the commented
`── Upstream PACS ──` section of `trust/.env.example`, and
[`xnat/README.md`](xnat/README.md#pacs-configuration) for what each one does):

```sh
XNAT_AETITLE=XNAT
PACS_HOST=orthanc
PACS_AETITLE=ORTHANC
PACS_QR_PORT=4242
```

`XNAT_AETITLE` is applied to XNAT's SCP receiver, its DQR calling AE and the C-MOVE destination it
hands the PACS, so all three agree and the PACS's return-leg association is addressed correctly.
`up-trust` / `up-trust-ec2` run a `require-xnat-aetitle` guard first: the variable may be absent
(the default `XNAT` applies) but **must not be present-and-empty** — that fails the bring-up naming
the kit file, before any data fixture is fetched. The remaining `PACS_*` and `DQR_*` variables
(`PACS_LABEL`, `PACS_SUPPORTS_EXTENDED_NEGOTIATIONS`, `PACS_AVAILABILITY_DAYS`/`_START`/`_END`,
`PACS_THREADS`, `PACS_UTILIZATION_PERCENT`, `DQR_MAX_PACS_REQUEST_ATTEMPTS`,
`DQR_RETRY_WAIT_SECONDS`) default to the mocked Orthanc plus an unthrottled retrieval window; a
real trust agrees them with its PACS manager.

The kit also carries the trust's **disclosure floor**:

```sh
COHORT_QUERY_THRESHOLD=10
```

This is the minimum cohort size the trust will release anything about. Cohort statistics below it are privacy-suppressed (a genuine zero and a small count are indistinguishable), and both row-level routes refuse outright — `/cohort/dataframe`, which supplies FL training data, and `/cohort/accession-ids`, which decides whose imaging is pulled into XNAT. Raise it to release less. It is the operator's setting, not the hub's: trusts need not agree on a value, and the hub cannot lower it. See [`data-access-api/README.md`](data-access-api/README.md#row-level-data-and-the-disclosure-threshold). The same schema serves dev trusts (GSTT/KCH against a local hub), on-prem trusts (against a prod hub), and laptop-against-prod testing — operator picks the kit code (`trust/.env.<CODE>.<env>`) and `make -C trust up-trust KIT=<CODE> PROD=<env>` handles the rest.

#### Site-enforced FL privacy policy (optional, NVFLARE only)

The kit file's Host-local profile can set `FL_SITE_PRIVACY_POLICY=percentile` (plus optional
`FL_SITE_PRIVACY_*` parameters — see the commented block in `trust/.env.example`). The fl-client entrypoint
renders these into the client's NVFLARE `local/privacy.json` at container start, so THIS trust's
update-privacy filter is applied to every outgoing model update regardless of the researcher's app config
(site filters run before app filters and jobs cannot opt out). Unset = no site policy (app-level filters
only, the previous behavior). Invalid values stop the fl-client at startup — fail closed. Apply changes with
`make -C trust up-fl-clients-kit KIT=<CODE>` (`down-fl-clients-kit KIT=<CODE>` stops and removes
just that trust's clients; `up-fl-clients` / `down-fl-clients` do the same across every kit for
this env, leaving the rest of each trust stack up); the fl-client log then shows
`[site-privacy] site privacy policy ACTIVE: ...`. Details: `docs/source/components/component-fl-nets.rst`
("Site-enforced privacy policy").

#### Trust governance policy (optional)

The two controls above are the trust's runtime access policy, and both are single settings compiled
into the services. A trust that wants to state them together — or to vary a rule by project — can
write a governance document instead and point `ACCESS_POLICY_FILE` at it from the kit file. Start
from [`governance.example.toml`](governance.example.toml), which is a worked example of every
section.

One file, three sections, each read by the service that enforces it:

| Section | Read by | Replaces |
|---|---|---|
| `[disclosure]` | data-access-api | `COHORT_QUERY_THRESHOLD` (may raise it, never lower it) |
| `[access]` | data-access-api | nothing — new: permit/deny rules over project + operation |
| `[fl_privacy]` | fl-client | `FL_SITE_PRIVACY_*` (wins when both are set; the log names the source) |

The document is **optional and additive**. Unset means the platform defaults apply and behaviour is
exactly as before — the two variables above stay the only controls, so no existing trust has to
change anything. Nothing here can weaken a trust's posture: `min_cohort_size` may only raise the
threshold, and an action no rule mentions keeps its existing behaviour (which is what lets a trust
adopt one rule without enumerating everything).

Validation is strict and fails closed. An unknown key, a misspelt action, or a threshold below the
kit's floor stops the service at startup rather than being ignored — a silently-dropped access rule
is worse than no rule, because the operator believes it is in force. Validate before applying:

```sh
make -C trust check-governance KIT=<CODE>
```

Apply with `make -C trust up-trust KIT=<CODE>` (and `up-fl-clients-kit KIT=<CODE>` for the
`[fl_privacy]` section). The document is mounted read-only — a service can never rewrite its own
policy — and is operator-owned: the hub cannot set, read, or override it. A denied request is
answered with the same fixed refusal as a below-threshold cohort, so a caller cannot use it to probe
the trust's configuration; the rule id that caused the denial goes to the trust's own log.

### 3. Start the trust against the hub

```sh
make -C trust down-trust KIT=GSTT   # if a previous GSTT stack is running
make -C trust up-trust KIT=GSTT
```

On an on-prem host provisioned by the on-prem playbook, prefix these with `sudo -E` — the login
user is deliberately not in the docker group (see
[trust/deploy/ansible/README.md](deploy/ansible/README.md)). Dev workstations are
unaffected.

The trust-api container authenticates with its `TRUST_API_KEY` and posts a heartbeat to `POST /trust/heartbeat` (no name segment — the hub resolves the trust's identity from the API key). The Connection status page flips the row online within ~30s.

### Cleanup / lost kit

The plaintext keys aren't recoverable — only the hash is on disk. If you didn't save the kit, the only options are:

* delete the row (`DELETE FROM trust WHERE name='<name>';` against `flip-db`, after freeing the slot: `UPDATE fl_kit_slot SET assigned_to_trust_id = NULL, assigned_at = NULL WHERE assigned_to_trust_id = '<trust-id>';`) and re-register, or
* re-register with `make register-trust KIT=<CODE>`, which mints fresh keys and rewrites the kit file — this also rotates the keys.

## Data versions

All mock trust data — the canonical OMOP tables and the per-project DICOM sets — comes from one
public Hugging Face dataset,
[`aicentreflip/trust-data`](https://huggingface.co/datasets/aicentreflip/trust-data), which
holds **exactly one copy of every artefact at an unversioned path**, one pair per project:

```
omop-csv/<project>/*.csv       omop-csv/<project>/source/…        dicom/<project>.tar.gz
```

Not every project has a `dicom/` set. A project cut from open data — `spleen_project` and
`brain_mri_project`, both from the Medical Segmentation Decathlon — publishes only its tables and
the metadata table they were built from; its DICOMs are regenerated locally by a deterministic
converter (`fl-tutorials/datasets/`, FLIP#1221) and seeded from that tree
(`make -C fl-tutorials seed-<dataset> KIT=<CODE>`, which drives `seed-omop … CANONICAL_DIR=` and
`seed-orthanc … DICOM_SOURCE= TABLES_DIR=` here). `cxr_project` and `prostate_project` still ship
one, which is why the default `PROJECTS` a bring-up seeds is `cxr_project` alone.

A trust is stood up by **seeding** those into its running omop-db and Orthanc (`up-trust` does it,
see below); there are no volume snapshots to download. A **data version is a git tag on that
dataset**, and [`.data_version`](.data_version) in this directory pins one — a single pin for OMOP
and Orthanc together, because a tag describes the whole dataset state. Every consumer (`seed-omop` /
`seed-orthanc`, the spleen label uploader, the Ansible plays, the Helm chart) fetches
`resolve/<tag>/<path>`, so an old version stays reachable at its tag forever and is never
duplicated as a second directory or a suffixed filename. `HF_TRUST_DATA_REVISION` overrides the
tag everywhere (`main` to work against files uploaded but not tagged yet; a sha to freeze one).

Publishing a new version is one commit that replaces exactly the artefacts that changed, plus one
tag. An existing tag is never moved, so a published version means one set of bytes for good. The
commit and the tag are separate calls to the Hub: if the second fails, the bytes sit on `main` with
nothing pinning them and no consumer resolving them — re-run the same command to finish it.

```sh
uv run orthanc/publish_dicom.py --project … --revision main --out orthanc/dist/dicom/<project>.tar.gz
make publish-trust-data VERSION=20261001 DRY_RUN=1 \
  OMOP_CSV=omop-db/data/canonical DICOM=orthanc/dist/dicom/<project>.tar.gz [CARD=…]
make publish-trust-data VERSION=20261001 …            # for real; then set .data_version to 20261001
make publish-trust-data VERSION=20261001 OMOP_CSV=… DELETE=dicom/spleen_project.tar.gz   # retire a re-hosted set
```

`DELETE=<path in repo>` removes a file from `main` in the same commit (earlier tags keep it) — how
`dicom/spleen_project.tar.gz` went when spleen moved to local regeneration.

(`hf auth login` with write access to the dataset is needed.) Bumping `.data_version` is what
moves a checkout: the next `up-trust` (its `ensure-seeded` step) re-seeds the trust's projects at
the new tag — OMOP rows replaced project by project, PACS studies cleared and re-uploaded — and
every seed/enrichment run reads at the new tag.

### Seeding at bring-up

`up-trust` starts omop-db and Orthanc on empty, pre-created volumes and then runs
`ensure-seeded`, which loads `PROJECTS` (default `cxr_project` — the projects that publish a DICOM
set, see above) from the dataset at
the pinned version. Each half leaves a marker beside its store — `<omop dir>/../.seeded` and
`<orthanc parent>/.<storage dir>.seeded` — recording projects, partition and version; a marker
that matches means nothing to do, so a second `up` fetches and uploads nothing and the seeded
volumes simply persist on the host. A first bring-up on a fresh host is the one slow step:
roughly 2 GB of DICOM per trust posted through Orthanc's REST API (a few minutes); the OMOP half
takes seconds. The DICOM vocabulary is loaded with the rows; the licensed core vocabulary is still
the separate credentialed `make -C trust/omop-db load-omop-vocab` step.

```sh
make -C trust ensure-seeded KIT=GSTT PROJECTS="cxr_project"                   # what up-trust runs; safe to repeat
make -C trust seed KIT=GSTT PROJECTS="prostate_project"                       # add a project, unconditionally
```

**A host seeded before FLIP#1187 re-seeds once.** The PACS marker used to live *inside* the
storage directory (`<storage dir>/.seeded`) and now sits beside it, because the directory
itself is owned by Orthanc's uid. The new reader does not look at the old path, so the first
`up-trust` after this change finds no marker and re-seeds: the DICOM cache under
`trust/orthanc/volumes/dicom/` is keyed by data version and survives, so nothing is
re-downloaded, but every instance is re-posted (Orthanc answers `AlreadyStored`) — a few
minutes, once. The stale `.seeded` inside the storage directory can be deleted.

Recovering a half-loaded DICOM vocabulary: the loader skips itself when its scaffolding
concept is present, and that concept is committed before the bulk load, so a run killed in
between leaves a database that reports itself loaded and is skipped for good.
`make -C trust/omop-db load-dicom-vocab FORCE_DICOM_VOCAB=1` reloads over it. The same
variable reaches the Kubernetes hook (`trustData.seed.forceDicomVocab`) and the EC2 play
(`make -C deploy/providers/AWS seed-trust-data KIT=<CODE> FORCE_DICOM_VOCAB=1`).

### Which partition a trust is seeded with

`make -C trust seed KIT=<CODE>` loads the OMOP `source_trust` partition matching the trust's **FL
kit slot** — partition 1 into the trust holding `Trust_1`, and so on. That is a default, not an
invariant: the kit slot and the OMOP partition are separate axes that happen to line up on the
shipped GSTT/KCH roster. Slots are claimed from a pool in registration order, so a re-registered
trust, or a third one, can hold slot 2 while the data meant for it is partition 1.

Seeded the wrong way round nothing complains — the OMOP rows and the PACS studies are selected by
the same column, so they still agree with each other; they just belong to another institution.
Override with `SOURCE_TRUST` when the two differ:

```sh
make -C trust seed KIT=<CODE> SOURCE_TRUST=1        # slot stays as assigned; load partition 1
```

To seed the whole shipped dev roster in one go — `seed KIT=GSTT` then `seed KIT=KCH`, same
projects, same partition-by-slot default:

```sh
make -C trust seed-trusts PROJECTS="cxr_project"
```

`seed` itself is just `seed-omop` + `seed-orthanc`; run either half alone with
`make -C trust seed-omop KIT=<CODE> PROJECTS=…` / `seed-orthanc KIT=<CODE> PROJECTS=…`.

`SOURCE_TRUST` moves only the partition. The trust's volumes, ports and the `.seeded` marker stay
keyed to its kit slot, which is why it exists as its own variable rather than an override of
`TRUST_NUM`.

### Unseeding, and moving off a re-cut project

`make -C trust unseed KIT=<CODE> PROJECTS="…"` takes the listed projects *out* of a running trust —
OMOP rows by the person ids and PACS studies by the accessions the tables at
`HF_TRUST_DATA_REVISION` (or `CANONICAL_DIR=` / `TABLES_DIR=`) name — and loads nothing; every other
project stays. It exists for a project whose identities were re-cut (spleen at the FLIP#1221 tag:
every person id, accession and UID changed), where `seed-omop`'s default `--clean projects` would
delete by the *new* ids and leave the old rows beside them:

```sh
make -C trust unseed KIT=GSTT PROJECTS=spleen_project HF_TRUST_DATA_REVISION=20260911   # the cut the trust holds
make -C fl-tutorials seed-spleen KIT=GSTT                                              # the new cut, from the local tree
```

`seed-omop CLEAN=all` is the blunt alternative (every project's rows go first); `populate` on the
build stack is the same loader with that mode.

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
   - EC2 trust: `make -C trust up-trust-ec2 KIT=<CODE> PROD=true` (stop it with
     `make -C trust down-trust-ec2 KIT=<CODE> PROD=true`)
   - On-prem trust: `sudo -E env PROD=true make -C trust up-trust KIT=<CODE>`
     (sudo: the provisioned login user is deliberately not in the docker group;
     `-E` keeps `$HOME` so root's docker reuses your GHCR login)
   - Laptop-against-prod: `make -C trust up-trust KIT=<CODE> PROD=true` (no sudo —
     your workstation isn't provisioned by the on-prem playbook)

   `PROD` names the hub you are joining, and with it the kit-file suffix the
   trust Makefiles read: `true` → `.env.<CODE>.production`, `stag` → `.stag`,
   and on an LZA estate (FLIP#749) `lza` → `.lza-prod`, `lza-stag` → `.lza-stag`.
   All four run the production compose and stack files; only `PROD` unset is
   the development stack.

   On-prem, `up-trust` runs `ensure-seeded` unconditionally, the same way it does in
   dev: a store with no `.seeded` marker beside it is seeded with the listed mock
   projects (anonymous Hugging Face download, no hub credentials) — OMOP via
   `--clean projects`, which replaces only those projects' own rows, and Orthanc by
   posting their studies. A real on-prem operator who points `OMOP_DATA_DIR` /
   `ORTHANC_STORAGE_DIR` at real data therefore gets the mock projects loaded
   alongside it on first bring-up; there is no switch to turn the seed off yet.

   `up-trust` is the **first-install** verb on every path, on-prem included: its
   XNAT step runs `xnat-reset`, which wipes the XNAT archive and database, and its
   `ensure-seeded` step re-seeds the listed projects whenever the seed markers
   differ from the kit (a `.data_version` bump or changed `PROJECTS`). A real
   on-prem operator runs `up-trust` **once**; every later move to a release goes
   through `upgrade-trust` (below), never `up-trust` or `restart-trust`.

### Upgrading to a release (FLIP#1204)

```bash
git fetch --tags origin && git checkout vX.Y.Z              # the checkout first: compose files + this verb come from it
sudo -E make upgrade-onprem-trust KIT=<slot>              # → the release the hub runs
sudo -E make upgrade-onprem-trust KIT=<slot> TAG=vX.Y.Z   # → a named release
```

Runs the readiness checklist, resolves the target (the hub's `/api/health`
`version`, or `TAG=`), refuses a release tag unless this checkout is at it
(`ALLOW_CHECKOUT_DRIFT=1` overrides — testing a branch), asks you to confirm
`site <current> → target <release>`,
writes the tag into your kit's Hub-shared block, pulls, recreates what changed,
and upgrades XNAT in place (database dump first, no reset); it never runs
`ensure-seeded`, so the OMOP / Orthanc stores are left as they are. Before the kit is touched
it asks the registry for every image at the target and refuses a tag any of them was
never built at (each `sha-` build is path-filtered; a release tag builds them all —
for a `sha-` move, `FL_TAG=` holds the FL client at its own build and `OMOP_DB_TAG` /
`ORTHANC_TAG` / `XNAT_TAG` in the kit do the same for the data services).
`FORCE=1` allows a downgrade, `YES=1` skips the prompt. The full runbook — ordering, refreshed kits,
Kubernetes and EC2 variants, rollback — is
`docs/source/sys-admin/admin-upgrading-sites.rst`.

### Refreshing shared values (when the hub admin rotates an AES key etc.)

When the hub admin rotates a shared value (AES key, FL backend, image tag),
they will run `make sync-trust-kit KIT=<CODE> PROD=true` on their side. That
produces an updated kit file with the new Hub-shared block; credentials are
preserved. The updated file is transmitted to you using the same out-of-band
channel. Replace **only the Hub-shared block** in your local copy (your
Host-local profile and Trust-local credentials stay yours), then re-apply:

```bash
sudo -E make upgrade-onprem-trust KIT=<slot> YES=1
```

That recreates only the containers whose configuration changed and leaves the
data alone. `restart-trust` would also work for the API containers but re-runs
`up-trust`'s XNAT reset — don't. A stale block is what the checklist's
*Hub-shared block current* row detects: trust-api compares its AES key with the
hub's on every heartbeat and reports the mismatch on its `/health`, so a rotated
key shows up there before it shows up as every task failing to decrypt.

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
