# AGENTS.md — Trust Services

## Architecture

Trust services run at each healthcare institution (cloud EC2 or on-prem). All trust communication is outbound — trusts poll the Central Hub; no inbound ports needed.

| Service | Port | Purpose |
| --------- | ------ | --------- |
| trust-api | 8020 | API gateway, polls hub for tasks, orchestrates trust |
| imaging-api | 8001 | DICOM image retrieval from PACS |
| data-access-api | 8010 | OMOP database queries for cohort analysis |
| fl-client | — | FL participant (connects outbound to FL server via NLB) |
| omop-db | 5432 | Mocked OMOP patient database (PostgreSQL); dir also holds the image build source + populate tooling (#834, see `omop-db/AGENTS.md`) and the `seed-omop` loader (#1100) |
| orthanc | 8042 | Mocked DICOM PACS server (UI/REST behind HTTP basic auth — kit file's `ORTHANC_USERNAME`/`ORTHANC_PASSWORD`; DICOM port 4242 is internal to the trust network and not bound to the host) |
| xnat | 8104/8105 | Mocked neuroimaging platform. `XNAT_PORT` (8104) is the **DICOM SCP receiver** port; `XNAT_WEB_PORT` (8105) is the web UI. Both are host-published — the receiver so a real PACS can C-STORE back in, dev included so it runs the same wiring — so the two must differ; the deploy refuses a collision (FLIP#993) |
| observability | 3000/3100 | Grafana + Loki monitoring stack |

## Kit file structure

Each `trust/.env.<CODE>.<env>` carries the trust's host-local profile,
trust-local credentials, and kit credentials. **Hub-shared vars have one source
per environment** — there is no copy to drift:

- **Dev:** the kit's Hub-shared block is **commented out** (inert). The values
  are inherited from the hub's `.env.development` (the single source) —
  `trust/Makefile` `-include`s it (dev only) and the root `make up` also exports
  it. So you edit a hub-shared var (e.g. `DOCKER_FL_TAG`, `AES_KEY_BASE64`) in
  `.env.development` and `make up`; no re-register, no stale kit copy.
- **Prod:** the kit carries the Hub-shared block **live** — a remote operator
  has no hub `.env`, so the kit is their only source. `trust/Makefile` stays
  kit-only in prod (no hub `.env` include); a stale/missing value fails loud.

Four sections, in order (the dev `.env.<CODE>.development.example` templates show
the commented dev form):

| Section | Owner | Touched by |
|---------|-------|-----------|
| Host-local profile | Operator | hand-edit (ports, bind dirs, optional `FL_SITE_PRIVACY_*` site privacy policy) |
| Trust-local credentials | Operator | hand-edit (passwords, service URLs) |
| Hub-shared (managed) | Hub admin | `register-trust KIT=<CODE>` (live in prod, commented in dev) / `sync-trust-kit KIT=<CODE>` (prod refresh) |
| Kit credentials (managed) | Hub | `register-trust` only — write-once; hub keeps only the hash |

The Hub-shared block is delimited by a sentinel comment
(`# ── Hub-shared (managed by register-trust / sync-trust-kits — do not edit) ──`)
that `scripts/distribute_trust_kits.py` and `scripts/sync_trust_kit.py`
match byte-for-byte. The exact key set is the `HUB_SHARED_ENV_KEYS` tuple in
`flip_api/scripts/register_trust.py` (`AES_KEY_BASE64`,
`CENTRAL_HUB_API_URL`, `TRUST_API_KEY_HEADER`, `FL_BACKEND`,
`FLOWER_KIT_DATE`, `FLARE_KIT_DATE`, `DOCKER_TAG`, `DOCKER_REGISTRY`,
`DOCKER_FL_TAG`, `DOCKER_FL_REGISTRY`, `NLB_SUBDOMAIN`, `FL_SERVER_PORT`).
`UPLOADED_FEDERATED_DATA_BUCKET` is hub-only (fl-server uploads aggregated
results to S3); `DOCKER_FL_CLIENT_NAME` is derived from `FL_BACKEND` by
`deploy/fl_backend.mk` (trust/Makefile includes it).

`make sync-trust-kit KIT=<CODE> PROD=<env>` refreshes the Hub-shared block in
`trust/.env.<CODE>.<env>` from the admin's local `$(MAIN_ENV_FILE)` without
rotating credentials. Implemented by `scripts/sync_trust_kit.py` (uv
PEP 723 script — stdlib only, no docker/jq/ECS round-trip). Works
identically across dev/stag/prod — the root Makefile's `include
$(MAIN_ENV_FILE)` + `export` populates os.environ before the script
runs, so `PROD=true` simply selects which env file to read. Run after
rotating `AES_KEY_BASE64`, bumping `DOCKER_TAG`, switching `FL_BACKEND`,
etc., then re-transmit the refreshed kit file to the remote operator
(out-of-band, same as initial distribution — SCP-via-SSM for EC2;
encrypted channel for on-prem).

For the on-prem flow, the admin scaffolds and fills the kit on their
workstation in two commands (prod AWS creds required):

1. `make new-trust TRUST_CODE=<CODE> TRUST_NAME="..." PROD=true`
   — scaffolds `trust/.env.<CODE>.production` from the base template
   (`trust/.env.example`).
2. `make register-trust KIT=<CODE> PROD=true` — registers on the prod hub and
   fills BOTH the Kit credentials AND the Hub-shared block in one step
   (replaces the old "paste 5 UI lines + separate `sync-trust-kit`").

Then `make -C deploy/providers/AWS package-onprem-trust-kit KIT=<CODE> PROD=true`
tarballs the populated kit file as-is + the operator's slice of the FL
participant kit S3 bucket into
`deploy/providers/AWS/build/trust-kits/flip-trust-kit-<slot>-<date>.tar.gz`.
The packager does NOT edit the kit file.

The operator extracts, copies `.env.<CODE>.production` into their checkout,
edits only the Host-local profile (sets `FL_KIT_DIR`, ports/dirs) and rotates
the Trust-local passwords, runs `sudo -E make onboard-onprem-trust KIT=<CODE> PROD=true`
for the readiness checklist (kit present, swarm active — the swarm check queries
the docker daemon, hence sudo; Hub-shared + Kit credentials populated, FL_KIT_DIR
exists + has the expected files), then `sudo -E make up-onprem-trust KIT=<CODE> PROD=true`.
Sudo because the provisioned login user is deliberately not in the docker group
(root-equivalent); `-E` preserves `$HOME` so root's docker reuses the operator's
GHCR login from `~/.docker/config.json`.

## Key Files

| File | Purpose |
|------|---------|
| `Makefile` | Trust stack orchestration (parameterized `up-trust KIT=<name>`) |
| `deploy/README.md` | Compose file matrix, the `--project-directory` rule these files depend on, and the external networks they join |
| `deploy/helm/` | The same trust stack as Helm chart `flip-trust` for Kubernetes (chart, Makefile, `sync_k8s_kit.py`, tests) |
| `deploy/ansible/onprem.yml` | Ansible play that provisions a site-owned Ubuntu host (Docker, `/opt/flip` dirs, uid rules) for the compose stack; on-prem twin of `deploy/providers/AWS/site.yml`. Driven by `make -C deploy/providers/AWS provision-local-trust`, which needs the hub env file the AWS Makefile parses at load — the known exception to "providers = Terraform only" |
| `deploy/compose_trust.development.yml` | Dev Docker Compose (pulls repo-built services from GHCR by default via `pull_policy: always`; `BUILD=true` rebuilds from the `build:` block instead) |
| `deploy/compose_trust.production.yml` | Prod Docker Compose (GHCR images; declares the `trust-local-{loki,grafana}-data` named volumes as defaults) |
| `deploy/compose_trust.{env}.{flower\|nvflare}.yml` | FL backend variants |
| `deploy/compose_trust.{env}.gpu.yml` | GPU passthrough overlay — added by `up-trust` / `up-fl-clients-kit` via `GPU_OVERRIDE` only when the kit's `NUM_AVAILABLE_GPUS > 0`; reserves host NVIDIA GPU(s) for the fl-client. `up-trust-ec2` never applies it (the EC2 t3.xlarge is GPU-less, so the fl-client is CPU-only there regardless of the kit) |
| `deploy/compose_trust.{env}.monailabel.yml` | Optional MONAI Label server (AI-assisted annotation in the XNAT OHIF viewer) — added by `up-trust` / `build` via `MONAILABEL_OVERRIDE` only when the kit (or CLI) sets `MONAI_LABEL=true`. Requires `NUM_AVAILABLE_GPUS > 0`; `up-trust` refuses otherwise. Dev builds from `trust/monailabel/`; production pulls `ghcr.io/londonaicentre/monailabel:${DOCKER_TAG}` (published by `docker_build_monailabel.yml`) (the OHIF viewer plugin it registers through ships by default in every mode). Bind-mounts the trust's XNAT archive read-only at the path `MONAI_LABEL_XNAT_ARCHIVE_DIR` resolves to — anchored at `xnat/` because the kit's `XNAT_DATA_DIR` is written relative to `trust/xnat/`. Published on `MONAI_LABEL_BIND_HOST` (default `127.0.0.1`) — the API is unauthenticated and holds the XNAT service account, so it is loopback-only until an operator fronts it through the XNAT nginx or firewalls an interface. `MONAI_LABEL=true` **also** gates `configure-export-mask.sh` in `xnat/Makefile`'s `xnat-configure` (the DICOM-SEG → NIfTI converter command + its site-wide subscription); enabling MONAI Label on an already-running trust needs a `make -C trust/xnat xnat-configure KIT=<CODE>` re-run. `up-trust-ec2` refuses `MONAI_LABEL=true` outright (no GPU on the t3.xlarge). See [README.md](README.md#monai-label-optional) |
| `deploy/compose_trust-1_override.yml` | Dev trust-1 host-port bindings |
| `.env.<CODE>.<env>` | Per-trust kit file, e.g. `.env.GSTT.development`, `.env.<CODE>.production` (TRUST_API_KEY, TRUST_INTERNAL_SERVICE_KEY, FL_KIT_SLOT, FL_KIT_SLOT_NUMBER, EXPECTED_TRUST_ID, host-local ports/dirs, **FL_KIT_DIR** — root of the FL participant kit, default `/opt/flip/fl-kit` matching the Ansible-staged EC2 path); gitignored. Templates: per-trust dev examples `.env.GSTT.development.example` / `.env.KCH.development.example`; the generic scaffold base `.env.example`, consumed by `make new-trust`. Same kit-file schema everywhere — `make -C trust up-trust KIT=<CODE> PROD=<env>` is the only dispatch |

## Commands (from `trust/`)

```bash
make up                        # Start the shipped dev trust stacks (GSTT + KCH)
make down                      # Stop all trusts
make up-trust KIT=GSTT         # Start one trust stack (also brings up its XNAT)
make down-trust KIT=GSTT       # Stop one trust stack
make restart-trust KIT=GSTT    # Restart one trust stack (down + up-trust — first-install semantics, resets XNAT)
make upgrade-trust KIT=<CODE> PROD=<env> [TAG=vX.Y.Z] [FORCE=1] [YES=1]  # Move a LIVE trust to a release, data intact (FLIP#1204): resolver pins the kit, pull, recreate what changed, upgrade-xnat in place. Never up-trust/restart-trust for this
make up-trust-ec2 KIT=GSTT     # Start one trust stack on a cloud EC2 host
make up-trust KIT=<CODE> PROD=true  # Start a trust pointing at a remote hub (on-prem hosts: prefix sudo -E — login user is not in the docker group)
make debug                     # Trust-1 in debug mode
make debug-trust-api           # Debug trust-api only
make debug-imaging-api         # Debug imaging-api only
make debug-data-access-api     # Debug data-access-api only
make tests                     # Run tests on all 3 API services
make build                     # Build all trust Docker images
make create-networks           # Create Docker overlay networks
make ensure-seeded KIT=GSTT [PROJECTS="…"]  # What up-trust runs after compose up: seed unless the markers already say so (#1187)
make seed KIT=GSTT PROJECTS="cxr_project"  # Seed a RUNNING trust unconditionally: OMOP rows + DICOMs by source_trust (#1100), from the published tag
make seed-trusts PROJECTS="…"  # Both dev trusts; seed-omop / seed-orthanc for one half; CLEAR=1, DRY_RUN=1 on the PACS half
make seed-omop KIT=GSTT PROJECTS=brain_mri_project CANONICAL_DIR=/abs/canonical   # local tables instead of the tag (#1221); CLEAN=all = wipe every project first
make seed-orthanc KIT=GSTT PROJECTS=brain_mri_project DICOM_SOURCE=/abs/dicom TABLES_DIR=/abs/canonical  # a regenerated DICOM tree (spleen, brain_mri: never published)
                               # …both wrapped by `make -C fl-tutorials seed-spleen|seed-brain-mri KIT=<CODE>`
make unseed KIT=GSTT PROJECTS=spleen_project HF_TRUST_DATA_REVISION=20260911  # take a project OUT (rows by its person ids, studies by its accessions, as those tables name them) — the move off a re-cut project; other projects untouched
make seed KIT=GSTT SOURCE_TRUST=1  # Override the OMOP partition; defaults to the FL kit slot, which is a convention, not an invariant (see README "Which partition a trust is seeded with")
make publish-trust-data VERSION=<tag> [OMOP_CSV=… DICOM=… CARD=… DELETE=…]  # ONE commit on aicentreflip/trust-data + ONE tag; then bump trust/.data_version (the single pin, OMOP + Orthanc). DELETE= retires a file from main (earlier tags keep it)
make test-trust-data-tools  # Three things: publisher pytest + ruff, shellcheck over seed_trust.sh, and the seed-marker contract harness (tests/test_seed_marker_contract.sh)
```

## Environment

- All runtime config comes from the kit file (`trust/.env.<KIT>`); no hub `.env.*` is included by `trust/Makefile` or `trust/xnat/Makefile`. `PROD` still selects the compose-file suffix (development / production) but no longer drives an env-file include.
- Trust identity: `TRUST_API_KEY` (per-trust, from the kit file `trust/.env.<CODE>.<env>`); optional `EXPECTED_TRUST_ID` self-check. The hub identifies the trust by API key alone.
- Encryption: `AES_KEY_BASE64` for trust-to-hub payload encryption (hub-shared; synced into the kit file).
- `DEBUG` is no longer inherited from a hub env file. `make debug` / `make debug-off` set it explicitly; `make up-trust` without an explicit `DEBUG=true` runs services in non-debug mode.
- Site-enforced FL privacy policy (NVFLARE only, FLIP#851): `FL_SITE_PRIVACY_POLICY=percentile` (+ optional `FL_SITE_PRIVACY_*` params, see `trust/.env.example`) in the kit's Host-local profile. Rendered into the fl-client's NVFLARE `local/privacy.json` at container start by `python -m flip.nvflare.site_policy` — composes on top of (runs before) any app-level filter, jobs can't opt out, invalid values fail the fl-client closed. Unset = no site policy (previous behavior). Apply with `make -C trust up-fl-clients-kit KIT=<CODE>`.
- The two shipped dev trusts (GSTT, KCH) have separate ports, networks, and data dirs. Their FL kit *slots* are still named `Trust_1` / `Trust_2` — those are the pre-provisioned FL participant-kit identities (cert CN for NVFLARE, supernode number for Flower), assigned to a trust by the hub at registration. A trust (GSTT) claims a slot (Trust_1); they are different things.
- Local trust uses `trust-local` project name to avoid port collisions

## XNAT and PACS Environment Variables

- `XNAT_PORT` / `XNAT_WEB_PORT` / `XNAT_AETITLE` — XNAT's DICOM SCP receiver port, its host-published
  web-UI port, and its AE title. `XNAT_PORT` was historically one variable doing both jobs, which is
  why host 8104 served Tomcat while the DICOM receiver's 8104 was an unpublished container port
  (FLIP#993). `XNAT_AETITLE` is applied to the SCP receiver, `dqrCallingAe`, and the C-MOVE
  destination in `ImportStudyRequest` — DQR matches that destination against a registered receiver by
  exact `AE:port`, so all three must agree and no translation is possible on that leg. Both ports are
  host-published — the receiver so a real PACS can complete the C-STORE return leg of a retrieval,
  and dev keeps the same wiring — so they must differ; the Makefile refuses to deploy if they
  collide. Dev allocation: 8104/8105 (GSTT), 8106/8107 (KCH).
- `PACS_HOST` / `PACS_AETITLE` / `PACS_QR_PORT` / `PACS_LABEL` — the upstream PACS, defaulting to the
  mocked Orthanc (`orthanc` / `ORTHANC` / `4242`). `PACS_QR_PORT` must be reachable *from the XNAT
  container*, not a host-published port — conflating the two is what the retired `PACS_DICOM_PORT`
  did (FLIP#822/#862). `configure-xnat.sh` updates an existing registration in place when the host or
  port drift, and imaging-api reads the PACS id from XNAT at runtime rather than assuming 1 —
  `configure-xnat.sh` keeps exactly one registration, so it is the sole one XNAT reports.
- `PACS_SUPPORTS_EXTENDED_NEGOTIATIONS` — whether the PACS supports relational queries / extended
  negotiation (default `true`). A capability of the PACS rather than a preference: one that does not
  support it rejects the association outright. Validated as literally `true` or `false` before it
  reaches jq, so a `yes` or a bare `1` fails naming the variable instead of registering the number 1.
- `PACS_AVAILABILITY_DAYS` / `_START` / `_END` / `PACS_THREADS` / `PACS_UTILIZATION_PERCENT` /
  `DQR_MAX_PACS_REQUEST_ATTEMPTS` / `DQR_RETRY_WAIT_SECONDS` — the retrieval throttle. A production
  PACS may refuse further associations after a certain volume, so the window and thread count are
  agreed with the trust's PACS manager. Defaults are all week, all day, one thread.

## Trust-internal Service Authentication

**Threat.** Imaging-api proxies privileged XNAT operations using a service account; data-access-api executes arbitrary SQL against OMOP using a service account. Without caller authentication on these APIs, any container on the trust Docker network — or any operator with SSM port-forward access — can drive XNAT-admin operations and run unrestricted OMOP queries. Both surfaces sit behind no inbound firewall on the trust host (everything is internal to the Docker network) and neither used to validate the caller's identity.

**Mitigation.** Every trust-internal call carries a shared-secret header. The header name comes from `TRUST_INTERNAL_SERVICE_KEY_HEADER` (default `X-Trust-Internal-Service-Key`), the value is the per-trust `TRUST_INTERNAL_SERVICE_KEY` from the trust's kit file (`trust/.env.<CODE>.<env>`). Receivers (imaging-api, data-access-api) compare the header against their own copy of the key with `hmac.compare_digest` (constant-time, defeats timing side-channels). Senders are trust-api, imaging-api (when calling data-access-api `/cohort/accession-ids`), and fl-client. The same key is held in plaintext by every trust-internal container — the trust boundary is the trust itself, not individual service-pairs within it. `/health` stays unauthenticated so liveness probes keep working.

**Per-trust scope.** Each trust gets a distinct key. A leak in Trust_1 cannot drive operations on Trust_2's APIs. The hub never sees these keys — they live only in trust-side env: `register_trust` writes `TRUST_INTERNAL_SERVICE_KEY` into the trust's kit file (`trust/.env.<CODE>.<env>`), which `trust/Makefile` `-include`s so every trust-internal container inherits it. This is deliberately distinct from the hub's `INTERNAL_SERVICE_KEY` (which protects fl-server → flip-api on the Central Hub).

**Generating keys.** The key is minted by `register_trust` (`make register-trusts`), which writes `TRUST_INTERNAL_SERVICE_KEY` into the trust's kit file. Re-register to rotate.

**Per-service code.** The auth check lives in each receiving service's `utils/internal_auth.py`:

- `trust/imaging-api/imaging_api/utils/internal_auth.py` — applied at the router level on every imaging-api router except `/health`.
- `trust/data-access-api/data_access_api/utils/internal_auth.py` — applied at the router level on `/cohort` (covers `/cohort`, `/cohort/dataframe`, `/cohort/accession-ids`).

The senders construct the header inline at call sites:

- `trust-api/trust_api/services/task_handlers.py::trust_internal_headers()` — used on outbound imaging-api and data-access-api calls.
- `imaging-api/imaging_api/services_external/data_access.py` — used on the outbound `/cohort/accession-ids` call.
- The `flip` Python package — lives at [`flip-utils/flip/`](../flip-utils/flip/) in this mono-repo, consumed by both the NVFLARE and Flower fl-client / fl-server images built from `fl-services/`. Wraps every fl-client call to imaging-api (`flip.get_by_accession_number`, etc.) and data-access-api (`flip.get_dataframe`). The package reads `TRUST_INTERNAL_SERVICE_KEY` from `os.environ` and forwards it on every request. **User-uploaded training code (`client_app.py`, `server_app.py`, anything under `tutorials/`) does not deal with the header directly** — it calls `flip.*` and the package handles transport-level auth.

## Trust data: seeding and versioning

**Trust data has one path: seeding (FLIP#1101/#1187).** `make up` starts each trust's omop-db and
Orthanc on empty, pre-created volumes and then runs `make -C trust ensure-seeded`, which loads
`PROJECTS` (default `cxr_project`: one list drives both halves, so it holds only projects the
dataset publishes a DICOM set for as well as tables — spleen and brain_mri regenerate theirs
locally since #1221) from the published canonical tables at the pinned
`trust/.data_version`: OMOP rows via `omop_db_tools.import_tables` (the DICOM vocabulary first,
skipped if present) and DICOMs via `trust/orthanc/seed_orthanc.py`, both selected by the same
`source_trust` column, so a trust's OMOP rows and the studies in its PACS agree by construction. Each
half leaves a marker beside its store (`volumes/Trust_<N>/.seeded`, `orthanc/.orthanc-storage-trust<N>.seeded`)
recording projects/partition/version; a matching marker means a later `up` fetches and uploads
nothing and the volumes just persist on the host, a differing one (a `.data_version` bump, a changed
`PROJECTS`) re-seeds those projects — rows replaced, studies cleared and re-uploaded. There is no
`FORCE` and no snapshot: the pre-#1187 `trust<N>_pgdata.tar` / `trust<N>_orthanc_data.tar` volume
tarballs, `update-*-data` scripts and `export-pgdata` are gone. The first bring-up on a fresh host
posts ~2 GB of DICOM per trust through Orthanc's REST API (minutes); the OMOP half is seconds. The
omop-db image's init scripts need `DATA_ACCESS_POSTGRES_PASSWORD` in the container env (compose
passes it) to create the read-only role on that first start. Adding a dataset means publishing its
`omop-csv/<project>/` tables and `dicom/<project>.tar.gz` (`trust/orthanc/publish_dicom.py` verifies
both agree before packaging). The FL simulator (`make -C fl-tutorials run-tutorial`) is a separate
path: LOCAL_DEV reads `fl-tutorials/data/` straight from disk and touches no trust service.

**One copy of every artefact; a data version is a git tag.** `aicentreflip/trust-data` holds each
file once, at an unversioned path on `main` (`omop-csv/<project>/`, `dicom/<project>.tar.gz`). A data
version is a tag on that dataset, and `trust/.data_version` is the ONE pin, for OMOP and Orthanc
together: every consumer (`omop_db_tools.dataset`, `seed_orthanc.py`, the
spleen uploader, Ansible, the Helm chart) fetches `resolve/<tag>/<path>`, so old versions stay
reachable at their tags forever and are never duplicated. `HF_TRUST_DATA_REVISION` overrides the
tag (`main` to work against content that is not tagged yet). Publishing a version is
`make -C trust publish-trust-data VERSION=<tag> …` — one commit on the dataset plus one tag —
then a bump of `trust/.data_version`. Never add a versioned filename or directory to the dataset.
