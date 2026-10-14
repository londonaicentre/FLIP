# AGENTS.md — imaging-api (DICOM Retrieval)

## Service Overview

FastAPI service for DICOM image retrieval via XNAT. Receives requests from trust-api, drives XNAT's DQR (DICOM Query-Retrieve) REST API to query and import from the PACS, and returns results.

## Key Patterns

- Communicates only with XNAT (`XNAT_URL`, `http://xnat-web:8080` on the trust network). It never opens a socket to Orthanc — DICOM reaches XNAT from the PACS via XNAT's own DQR plugin over DIMSE (port 4242)
- Receives internal requests from trust-api (task-driven flow) and from the fl-client via the `flip` package (`flip.get_by_accession_number` etc.); not directly exposed
- Every internal caller authenticates with the per-trust `TRUST_INTERNAL_SERVICE_KEY` header (see the root `AGENTS.md` "Trust-internal Service Authentication" section). `/health` stays unauthenticated
- DICOM-to-NIfTI conversion support
- One project is found with XNAT's own filter, never by listing every project (FLIP#1456 D1): `get_project` sends `GET /data/projects?format=json&ID=<id>`, `get_project_from_central_hub_project_id` sends `?secondary_ID=<hub uuid>`, and `create_project` lets XNAT's 409 on `POST /data/projects` be the existence check. XNAT turns a filter value with `*` or `%` into a SQL `LIKE` and splits it on `,` into an OR list, URL-encoded or not, so validate a value before it goes into a filter (XNAT's project-ID rule `[A-Za-z0-9_-]+`; the hub id as a canonical UUID), keep only rows that equal it exactly, and treat two exact matches as an error
- Downloads are cached on disk per (net, project, accession): extraction lands in `<BASE_IMAGES_DOWNLOAD_DIR>/<net_id>/<central_hub_project_id>/<accession_id>/` with a `.flip_complete-<assessor>-<resource>` sentinel written after successful extraction (`services/image_cache.py`); a present sentinel short-circuits the download route before any XNAT call (FLIP#953 — FL apps fetch the cohort per round). Invalidated by uploads to the same (project, accession), by `force_refresh=true`, and wiped with the net dir by NVFLARE's `CleanupImages`

## Commands

```bash
make test        # ruff + mypy + pytest (unit only — no integration target)
make unit_test   # Unit tests only
```
