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

## MONAI Label (optional)

MONAI Label adds AI-assisted annotation to the XNAT OHIF viewer: a **MONAI Label** menu in the
viewer's Masks panel that runs a segmentation model over the scan on screen and lets a user
correct the result interactively.

> **Prerequisite: the XNAT OHIF Viewer plugin** — the MONAI Label panel lives inside the viewer
> and registration targets its `/xapi/ohifaiaa` endpoint. FLIP ships the plugin by default in
> every deployment mode (see the plugin table in [xnat/README.md](xnat/README.md)), so there is
> no extra plugin step.

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
| `MONAI_LABEL_BIND_HOST` | `127.0.0.1` | Host interface `MONAI_LABEL_PORT` is published on. Loopback by default — see below. |
| `MONAI_LABEL_AUTH_ENABLE` | `false` | MONAI Label's own auth. Upstream default; enabling it needs an OAuth realm FLIP does not run. |

**`MONAI_LABEL_PUBLIC_URL` is the setting people get wrong.** XNAT stores this URL and hands it
to the OHIF viewer, which calls it **from the clinician's browser** — XNAT never proxies the
request. So it must resolve on the clinician's machine: a Docker service name, or `0.0.0.0`,
will not work. The default only works when the browser runs on the trust host itself. Two
further consequences on a real trust:

- If XNAT is served over **HTTPS**, the browser blocks a plain-`http://` MONAI Label URL as
  mixed content. Terminate TLS in front of MONAI Label, or serve it through the XNAT nginx so
  it is same-origin.
- The MONAI Label API is **unauthenticated** and holds this trust's XNAT service-account
  credentials. Anyone who can reach the port can enumerate every study the server can see,
  download identifiable DICOM, and write labels back to XNAT as an admin. `MONAI_LABEL_AUTH_ENABLE`
  is upstream's own switch, but turning it on requires an OAuth realm FLIP does not run — so
  the containment is the network, not the app.

  Because of that, **`MONAI_LABEL_BIND_HOST` defaults to `127.0.0.1`**: out of the box the port
  is published on loopback only, and the trust host gains no listener reachable from the
  network (the trust deployment model is otherwise strictly outbound — see
  [Deployment Architecture](../CLAUDE.md)). That default is deliberately *not* browser-usable
  from another machine. To give clinicians access, pick one:

  1. **Serve it through the XNAT nginx** (recommended) — same-origin with XNAT, so it also
     resolves the mixed-content problem above, and it inherits XNAT's TLS.
  2. **Set `MONAI_LABEL_BIND_HOST`** to an interface you have explicitly firewalled to the
     clinical network, and set `MONAI_LABEL_PUBLIC_URL` to match.

**Each user must switch the panel on themselves**, once per browser: in the viewer, *Options →
Preferences → Experimental*, tick **MONAILabel Tools**. A **MONAI Label** entry then appears in
the Masks panel. Registering the server is automatic, but this flag is not, and it cannot be
defaulted from the server — the viewer keeps it in the browser's `localStorage`. Until it is
ticked, a perfectly working server is simply absent from the viewer; that is the first thing to
check when it "isn't showing up", followed by `GET /xapi/ohifaiaa/servers` returning the URL and
that URL opening in the browser.

Two known stock-viewer defects (both client-side — the server returns valid masks throughout,
verifiable in the monailabel container's logs):

- *"Empty mask was returned by the model run"* from every interactive model (`sam_2d`,
  `deepgrow_*`) while `deepedit_seg` still works: the tab's segment state has gone stale —
  **reload the tab**.
- Interactive (DeepGrow-type) results always land in the **first segment**, regardless of which
  segment is selected — the viewer routes the mask through an active-segment index that segment
  selection does not update (observed in the OHIF **viewer frontend** 3.7.2 that ships inside
  XNAT OHIF **plugin** 3.8.0; the 3.7.0 frontend did not have the reworked segment store —
  note the frontend and plugin carry separate version numbers). Until fixed upstream, treat
  SAM/DeepGrow as single-target — rename the first segment afterwards — and use `deepedit_seg`
  for multi-organ work.

### Getting labels back out: the `export_mask` pipeline

A mask saved in the viewer lands in XNAT as a **DICOM-SEG assessor**, which FL training cannot
read — it consumes NIfTI. `trust/xnat/xnat/config/configure-export-mask.sh` closes that gap: it
registers an `export_mask` container-service command plus a site-wide event subscription on
image-assessor creation, so every saved segmentation is converted to NIfTI and uploaded back to
the session automatically. Nothing upstream does this step.

Two things to know:

- **It is registered only when `MONAI_LABEL=true`.** `make xnat-configure` gates it (see
  `xnat-configure-export-mask` in `trust/xnat/Makefile`), so a trust without MONAI Label gets
  neither the converter command nor the subscription. **If you enable MONAI Label on a trust
  that was already up, re-run `make -C trust/xnat xnat-configure KIT=<CODE>`** (or bring XNAT
  up again) — otherwise the annotation UI works but masks are never converted.
- **The converter image is currently `atriaybagur/aic-ohif-dicomseg-to-nifti:latest`** — a
  personal Docker Hub namespace on a mutable tag, pulled and run on the trust network with
  XNAT admin credentials injected by the container service. Moving it to
  `ghcr.io/londonaicentre` on an immutable digest is outstanding; until then, treat enabling
  MONAI Label as also trusting that image.

Notes:

- **SAM is always on**, independently of `MONAI_LABEL_MODELS`: the radiology app registers
  `sam_2d` and `sam_3d` (interactive click-to-segment) whenever the `sam2` package is
  importable. Their checkpoint is a further ~900 MB fetched from HuggingFace on first start,
  into the same persisted model directory. Upstream's `--conf sam2 false` would disable them,
  but `entrypoint.sh` passes only `--conf models`, so there is currently no way to turn SAM off
  from the kit file — it would need an entrypoint change.
- The server reads DICOM straight off this trust's XNAT archive (mounted read-only), falling
  back to HTTP downloads only for scans it cannot resolve there.
- Pretrained weights are fetched on first start and persisted in the `monailabel-models`
  volume, so a container recreate does not re-download them.
- Production (`PROD=true|stag`) runs the published `ghcr.io/londonaicentre/monailabel` image
  (built by `docker_build_monailabel.yml`; dev builds locally from `trust/monailabel/`).

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
