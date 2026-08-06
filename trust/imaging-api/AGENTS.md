# AGENTS.md — imaging-api (DICOM Retrieval)

## Service Overview

FastAPI service for DICOM image retrieval via XNAT. Receives requests from trust-api, drives XNAT's DQR (DICOM Query-Retrieve) REST API to query and import from the PACS, and returns results.

## Key Patterns

- Communicates only with XNAT (`XNAT_URL`, `http://xnat-web:8080` on the trust network). It never opens a socket to Orthanc — DICOM reaches XNAT from the PACS via XNAT's own DQR plugin over DIMSE (port 4242)
- Receives internal requests from trust-api (task-driven flow) and from the fl-client via the `flip` package (`flip.get_by_accession_number` etc.); not directly exposed
- Every internal caller authenticates with the per-trust `TRUST_INTERNAL_SERVICE_KEY` header (see the root `AGENTS.md` "Trust-internal Service Authentication" section). `/health` stays unauthenticated
- DICOM-to-NIfTI conversion support

## Commands

```bash
make test        # ruff + mypy + pytest (unit only — no integration target)
make unit_test   # Unit tests only
```
