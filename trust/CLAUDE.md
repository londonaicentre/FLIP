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

## Key Files

| File | Purpose |
|------|---------|
| `Makefile` | Trust stack orchestration (up/down/debug for trust-1, trust-2, local) |
| `compose_trust.development.yml` | Dev Docker Compose (builds from source) |
| `compose_trust.production.yml` | Prod Docker Compose (GHCR images) |
| `compose_trust.{env}.{flower\|nvflare}.yml` | FL backend variants |
| `compose_trust.local.yml` | On-prem trust override |

## Commands (from `trust/`)

```bash
make up                        # Start both trust stacks (trust-1 + trust-2)
make down                      # Stop all trusts
make up-trust-1                # Start trust-1 only
make up-trust-2                # Start trust-2 only
make up-local-trust            # Start on-prem local trust
make debug                     # Trust-1 in debug mode
make debug-trust-api           # Debug trust-api only
make debug-imaging-api         # Debug imaging-api only
make debug-data-access-api     # Debug data-access-api only
make tests                     # Run tests on all 3 API services
make build                     # Build all trust Docker images
make restart-trust-1           # Restart trust-1
make create-networks           # Create Docker overlay networks
```

## Environment

- `MAIN_ENV_FILE` resolves from PROD flag: `.env.development`, `.env.stag`, or `.env.production`
- Trust identity: `TRUST_NAME`, `TRUST_API_KEY` (per-trust, from `TRUST_API_KEYS` JSON)
- Encryption: `AES_KEY_BASE64` for trust-to-hub payload encryption
- Two trust instances (Trust_1, Trust_2) have separate ports, networks, and data dirs
- Local trust uses `trust-local` project name to avoid port collisions
