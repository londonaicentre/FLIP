# AGENTS.md — Trust Services

## Architecture

Trust services run at each healthcare institution (cloud EC2 or on-prem). All trust communication is outbound — trusts poll the Central Hub; no inbound ports needed.

| Service | Port | Purpose |
| --------- | ------ | --------- |
| trust-api | 8020 | API gateway, polls hub for tasks, orchestrates trust |
| imaging-api | 8001 | DICOM image retrieval from PACS |
| data-access-api | 8010 | OMOP database queries for cohort analysis |
| fl-client | — | FL participant (connects outbound to FL server via NLB) |
| omop-db | 5432 | Mocked OMOP patient database (PostgreSQL); dir also holds the image build source + populate tooling (#834, see `omop-db/AGENTS.md`) |
| orthanc | 8042 | Mocked DICOM PACS server (UI/REST behind HTTP basic auth — kit file's `ORTHANC_USERNAME`/`ORTHANC_PASSWORD`; DICOM port 4242 is internal to the trust network and not bound to the host) |
| xnat | 8104 | Mocked neuroimaging platform. `XNAT_PORT` is the **DICOM SCP receiver** port; `XNAT_WEB_PORT` (also 8104 by default) is the host-published web UI. The receiver is published only with `REAL_PACS=true`, so the two must differ then (FLIP#993) |
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
| Host-local profile | Operator | hand-edit (ports, bind dirs) |
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
| `deploy/compose_trust.development.yml` | Dev Docker Compose (pulls repo-built services from GHCR by default via `pull_policy: always`; `BUILD=true` rebuilds from the `build:` block instead) |
| `deploy/compose_trust.production.yml` | Prod Docker Compose (GHCR images; declares the `trust-local-{loki,grafana}-data` named volumes as defaults) |
| `deploy/compose_trust.{env}.{flower\|nvflare}.yml` | FL backend variants |
| `deploy/compose_trust.{env}.gpu.yml` | GPU passthrough overlay — added by `up-trust` / `up-fl-clients-kit` via `GPU_OVERRIDE` only when the kit's `NUM_AVAILABLE_GPUS > 0`; reserves host NVIDIA GPU(s) for the fl-client. `up-trust-ec2` never applies it (the EC2 t3.xlarge is GPU-less, so the fl-client is CPU-only there regardless of the kit) |
| `deploy/compose_trust-1_override.yml` | Dev trust-1 host-port bindings |
| `.env.<CODE>.<env>` | Per-trust kit file, e.g. `.env.GSTT.development`, `.env.<CODE>.production` (TRUST_API_KEY, TRUST_INTERNAL_SERVICE_KEY, FL_KIT_SLOT, FL_KIT_SLOT_NUMBER, EXPECTED_TRUST_ID, host-local ports/dirs, **FL_KIT_DIR** — root of the FL participant kit, default `/opt/flip/fl-kit` matching the Ansible-staged EC2 path); gitignored. Templates: per-trust dev examples `.env.GSTT.development.example` / `.env.KCH.development.example`; the generic scaffold base `.env.example`, consumed by `make new-trust`. Same kit-file schema everywhere — `make -C trust up-trust KIT=<CODE> PROD=<env>` is the only dispatch |

## Commands (from `trust/`)

```bash
make up                        # Start the shipped dev trust stacks (GSTT + KCH)
make down                      # Stop all trusts
make up-trust KIT=GSTT         # Start one trust stack (also brings up its XNAT)
make down-trust KIT=GSTT       # Stop one trust stack
make restart-trust KIT=GSTT    # Restart one trust stack
make up-trust-ec2 KIT=GSTT     # Start one trust stack on a cloud EC2 host
make up-trust KIT=<CODE> PROD=true  # Start a trust pointing at a remote hub (on-prem hosts: prefix sudo -E — login user is not in the docker group)
make debug                     # Trust-1 in debug mode
make debug-trust-api           # Debug trust-api only
make debug-imaging-api         # Debug imaging-api only
make debug-data-access-api     # Debug data-access-api only
make tests                     # Run tests on all 3 API services
make build                     # Build all trust Docker images
make create-networks           # Create Docker overlay networks
make update-omop-data          # Download/extract mock OMOP data (both trusts)
make update-omop-data TRUST=1  # Trust_1 only
make update-orthanc-data       # Download/extract mock DICOM data (both trusts)
make update-orthanc-data TRUST=1  # Trust_1 only
```

## Environment

- All runtime config comes from the kit file (`trust/.env.<KIT>`); no hub `.env.*` is included by `trust/Makefile` or `trust/xnat/Makefile`. `PROD` still selects the compose-file suffix (development / production) but no longer drives an env-file include.
- Trust identity: `TRUST_API_KEY` (per-trust, from the kit file `trust/.env.<CODE>.<env>`); optional `EXPECTED_TRUST_ID` self-check. The hub identifies the trust by API key alone.
- Encryption: `AES_KEY_BASE64` for trust-to-hub payload encryption (hub-shared; synced into the kit file).
- `DEBUG` is no longer inherited from a hub env file. `make debug` / `make debug-off` set it explicitly; `make up-trust` without an explicit `DEBUG=true` runs services in non-debug mode.
- The two shipped dev trusts (GSTT, KCH) have separate ports, networks, and data dirs. Their FL kit *slots* are still named `Trust_1` / `Trust_2` — those are the pre-provisioned FL participant-kit identities (cert CN for NVFLARE, supernode number for Flower), assigned to a trust by the hub at registration. A trust (GSTT) claims a slot (Trust_1); they are different things.
- Local trust uses `trust-local` project name to avoid port collisions
