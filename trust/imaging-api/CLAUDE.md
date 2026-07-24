# CLAUDE.md — imaging-api (DICOM Retrieval)

## Service Overview

FastAPI service for DICOM image retrieval from PACS (Orthanc/XNAT). Receives requests from trust-api, queries PACS, retrieves images, and returns results.

## Key Patterns

- Communicates with Orthanc (port 4242) and XNAT (port 8104) on the trust network
- Receives internal requests from trust-api (task-driven flow) and from the fl-client via the `flip` package (`flip.get_by_accession_number` etc.); not directly exposed
- Every internal caller authenticates with the per-trust `TRUST_INTERNAL_SERVICE_KEY` header (see the root `CLAUDE.md` "Trust-internal Service Authentication" section). `/health` stays unauthenticated
- DICOM-to-NIfTI conversion support

## Commands

```bash
make test        # ruff + mypy + pytest (unit only — no integration target)
make unit_test   # Unit tests only
```
