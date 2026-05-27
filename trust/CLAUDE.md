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
needs this file (no hub `.env`). Four sections, in order:

| Section | Owner | Touched by |
|---------|-------|-----------|
| Host-local profile | Operator | hand-edit (ports, bind dirs) |
| Trust-local credentials | Operator | hand-edit (passwords, service URLs) |
| Hub-shared (managed) | Hub admin | `make register-trust-N` / `make sync-trust-kit-N` |
| Kit credentials (managed) | Hub | `make register-trust-N` only — write-once; hub keeps only the hash |

`make sync-trust-kit-N` refreshes the Hub-shared block from the hub's
`MAIN_ENV_FILE` without touching credentials. Run after rotating
`AES_KEY_BASE64`, bumping `DOCKER_FL_TAG`, switching `FL_BACKEND`, etc., then
re-transmit the refreshed kit file to the remote operator (out-of-band, same
as initial distribution — SCP-via-SSM for EC2; encrypted channel for on-prem).

## Key Files

| File | Purpose |
|------|---------|
| `Makefile` | Trust stack orchestration (parameterized `up-trust KIT=<name>`, plus local) |
| `deploy/compose_trust.development.yml` | Dev Docker Compose (builds from source) |
| `deploy/compose_trust.production.yml` | Prod Docker Compose (GHCR images) |
| `deploy/compose_trust.{env}.{flower\|nvflare}.yml` | FL backend variants |
| `deploy/compose_trust.local.yml` | On-prem trust override |
| `deploy/compose_trust-1_override.yml` | Dev trust-1 host-port bindings |
| `.env.Trust_1` / `.env.Trust_2` / `.env.Trust_Local` | Per-trust kit file (TRUST_API_KEY, TRUST_INTERNAL_SERVICE_KEY, FL_KIT_SLOT, FL_KIT_SLOT_NUMBER, EXPECTED_TRUST_ID, host-local ports/dirs); gitignored, templates `.env.Trust_*.example`. `Trust_Local` is the on-prem trust (selected by `LOCAL_TRUST_NAME`); its `FL_KIT_SLOT` is decoupled from the file name |

## Commands (from `trust/`)

```bash
make up                        # Start both trust stacks (Trust_1 + Trust_2)
make down                      # Stop all trusts
make up-trust KIT=Trust_1      # Start one trust stack (also brings up its XNAT)
make down-trust KIT=Trust_1    # Stop one trust stack
make restart-trust KIT=Trust_1 # Restart one trust stack
make up-trust-ec2 KIT=Trust_1  # Start one trust stack on a cloud EC2 host
make up-local-trust            # Start on-prem local trust
make debug                     # Trust-1 in debug mode
make debug-trust-api           # Debug trust-api only
make debug-imaging-api         # Debug imaging-api only
make debug-data-access-api     # Debug data-access-api only
make tests                     # Run tests on all 3 API services
make build                     # Build all trust Docker images
make create-networks           # Create Docker overlay networks
```

## Environment

- `MAIN_ENV_FILE` resolves from PROD flag: `.env.development`, `.env.stag`, or `.env.production`
- Trust identity: `TRUST_API_KEY` (per-trust, from the kit file `trust/.env.<slot>`); optional `EXPECTED_TRUST_ID` self-check. The hub identifies the trust by API key alone.
- Encryption: `AES_KEY_BASE64` for trust-to-hub payload encryption
- Two trust instances (Trust_1, Trust_2) have separate ports, networks, and data dirs
- Local trust uses `trust-local` project name to avoid port collisions
