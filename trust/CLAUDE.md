# CLAUDE.md — Trust Services

## Architecture

Trust services run at each healthcare institution (cloud EC2 or on-prem). All trust communication is outbound — trusts poll the Central Hub; no inbound ports needed.

| Service | Port | Purpose |
|---------|------|---------|
| trust-api | 8020 | API gateway, polls hub for tasks, orchestrates trust |
| imaging-api | 8001 | DICOM image retrieval from PACS |
| data-access-api | 8010 | OMOP database queries for cohort analysis |
| fl-client | — | FL participant (connects outbound to FL server via NLB) |
| omop-db | 5432 | Mocked OMOP patient database (PostgreSQL) |
| orthanc | 4242 | Mocked DICOM PACS server |
| xnat | 8104 | Mocked neuroimaging platform |
| observability | 3000/3100 | Grafana + Loki monitoring stack |

## Kit file structure

Each `trust/.env.<KIT>` is a self-contained config — a trust operator only
needs this file (no hub `.env`). `trust/Makefile` deliberately does NOT
include any `.env.*` from the repo root; the kit file is the single source
of truth at runtime, so a stale or missing entry fails loud instead of
silently falling back to a hub value.

Four sections, in order (the templates `.env.Trust_*.example` carry the
same layout):

| Section | Owner | Touched by |
|---------|-------|-----------|
| Host-local profile | Operator | hand-edit (ports, bind dirs) |
| Trust-local credentials | Operator | hand-edit (passwords, service URLs) |
| Hub-shared (managed) | Hub admin | `make register-trust-N` / `make sync-trust-kit-N` |
| Kit credentials (managed) | Hub | `make register-trust-N` only — write-once; hub keeps only the hash |

The Hub-shared block is delimited by a sentinel comment
(`# ── Hub-shared (managed by register-trust / sync-trust-kits — do not edit) ──`)
that `scripts/distribute-trust-kits.sh` and `scripts/sync-trust-kits.sh`
match byte-for-byte. The exact key set is the `HUB_SHARED_ENV_KEYS` tuple in
`flip_api/scripts/register_trust.py` (`AES_KEY_BASE64`,
`CENTRAL_HUB_API_URL`, `TRUST_API_KEY_HEADER`, `FL_BACKEND`,
`FLOWER_KIT_DATE`, `FLARE_KIT_DATE`, `DOCKER_TAG`, `DOCKER_REGISTRY`,
`DOCKER_FL_TAG`, `DOCKER_FL_REGISTRY`, `DOCKER_FL_CLIENT_NAME`,
`UPLOADED_FEDERATED_DATA_BUCKET`, `NLB_SUBDOMAIN`, `FL_SERVER_PORT`).

`make sync-trust-kit-N` refreshes the Hub-shared block without rotating
credentials. Run after rotating `AES_KEY_BASE64`, bumping `DOCKER_TAG`,
switching `FL_BACKEND`, etc., then re-transmit the refreshed kit file to the
remote operator (out-of-band, same as initial distribution — SCP-via-SSM
for EC2; encrypted channel for on-prem).

For the on-prem flow, the admin populates `trust/.env.<slot>` by hand
on their workstation:

1. UI → Add Trust → paste the 5 modal lines into the Kit credentials
   section, replacing `<run-make-register-trusts>` placeholders.
2. `make sync-trust-kit-N` (from repo root) — fills the Hub-shared block,
   replacing `<run-make-sync-trust-kit>` placeholders.

Then `make -C deploy/providers/AWS package-onprem-trust-kit KIT=<slot>`
tarballs the populated kit file as-is + the operator's slice of the FL
participant kit S3 bucket into
`deploy/providers/AWS/build/trust-kits/flip-trust-kit-<slot>-<date>.tar.gz`.
The packager does NOT edit the kit file.

The operator extracts, copies the `.env.<slot>` into their checkout, edits
only the Host-local profile (sets `FL_KIT_DIR`, ports/dirs), runs
`make onboard-onprem-trust KIT=<slot>` for the readiness checklist (kit
present, swarm active, Hub-shared + Kit credentials populated, FL_KIT_DIR
exists + has the expected files), then `make up-onprem-trust KIT=<slot>`.
They never touch the prod UI directly — the admin uses it on their behalf
because the Hub-shared block + FL kit S3 slice both need prod AWS creds
the operator does not have.

## Key Files

| File | Purpose |
|------|---------|
| `Makefile` | Trust stack orchestration (parameterized `up-trust KIT=<name>`) |
| `deploy/compose_trust.development.yml` | Dev Docker Compose (builds from source) |
| `deploy/compose_trust.production.yml` | Prod Docker Compose (GHCR images; declares the `trust-local-{loki,grafana}-data` named volumes as defaults) |
| `deploy/compose_trust.{env}.{flower\|nvflare}.yml` | FL backend variants |
| `deploy/compose_trust-1_override.yml` | Dev trust-1 host-port bindings |
| `.env.Trust_1` / `.env.Trust_2` / `.env.<slot>` | Per-trust kit file (TRUST_API_KEY, TRUST_INTERNAL_SERVICE_KEY, FL_KIT_SLOT, FL_KIT_SLOT_NUMBER, EXPECTED_TRUST_ID, host-local ports/dirs, **FL_KIT_DIR** — root of the FL participant kit, default `/opt/flip/fl-kit` matching the Ansible-staged EC2 path); gitignored. Templates: `.env.Trust_1.example`, `.env.Trust_2.example` for dev defaults; `.env.Trust_2.production.example` for the prod on-prem flavor (FLIP-prod BDMS slot; copy to `.env.Trust_2` for a dedicated host or `.env.Trust_2_prod` to coexist with a dev Trust_2 on the same laptop). Same kit-file schema everywhere — `make -C trust up-trust KIT=<slot> PROD=<env>` is the only dispatch |

## Commands (from `trust/`)

```bash
make up                        # Start both trust stacks (Trust_1 + Trust_2)
make down                      # Stop all trusts
make up-trust KIT=Trust_1      # Start one trust stack (also brings up its XNAT)
make down-trust KIT=Trust_1    # Stop one trust stack
make restart-trust KIT=Trust_1 # Restart one trust stack
make up-trust-ec2 KIT=Trust_1  # Start one trust stack on a cloud EC2 host
make up-trust KIT=Trust_2 PROD=true  # Start a trust pointing at a remote hub (e.g. on-prem)
make debug                     # Trust-1 in debug mode
make debug-trust-api           # Debug trust-api only
make debug-imaging-api         # Debug imaging-api only
make debug-data-access-api     # Debug data-access-api only
make tests                     # Run tests on all 3 API services
make build                     # Build all trust Docker images
make create-networks           # Create Docker overlay networks
```

## Environment

- All runtime config comes from the kit file (`trust/.env.<KIT>`); no hub `.env.*` is included by `trust/Makefile` or `trust/xnat/Makefile`. `PROD` still selects the compose-file suffix (development / production) but no longer drives an env-file include.
- Trust identity: `TRUST_API_KEY` (per-trust, from the kit file `trust/.env.<slot>`); optional `EXPECTED_TRUST_ID` self-check. The hub identifies the trust by API key alone.
- Encryption: `AES_KEY_BASE64` for trust-to-hub payload encryption (hub-shared; synced into the kit file).
- `DEBUG` is no longer inherited from a hub env file. `make debug` / `make debug-off` set it explicitly; `make up-trust` without an explicit `DEBUG=true` runs services in non-debug mode.
- Two trust instances (Trust_1, Trust_2) have separate ports, networks, and data dirs
- Local trust uses `trust-local` project name to avoid port collisions
