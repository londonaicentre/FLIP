# AGENTS.md — imaging-api (DICOM Retrieval)

## Service Overview

FastAPI service for DICOM image retrieval via XNAT. Receives requests from trust-api, drives XNAT's DQR (DICOM Query-Retrieve) REST API to query and import from the PACS, and returns results.

## Key Patterns

- Communicates only with XNAT (`XNAT_URL`, `http://xnat-web:8080` on the trust network). It never opens a socket to Orthanc — DICOM reaches XNAT from the PACS via XNAT's own DQR plugin over DIMSE (port 4242)
- One project is looked up with XNAT's filter (`GET /data/projects?ID=` / `?secondary_ID=`), never a full listing: validate the value and keep only exact matches, because XNAT's filter treats `*`/`%` as wildcards and `,` as a list
- Receives internal requests from trust-api (task-driven flow) and from the fl-client via the `flip` package (`flip.get_by_accession_number` etc.); not directly exposed
- Every internal caller authenticates with the per-trust `TRUST_INTERNAL_SERVICE_KEY` header (see the root `AGENTS.md` "Trust-internal Service Authentication" section). `/health` stays unauthenticated
- DICOM-to-NIfTI conversion support
- Never reads XNAT's database: import status comes from DQR's and XNAT's REST listings (`services/import_requests.py`, FLIP#1456 B1)
- Downloads are cached on disk per (net, project, accession): extraction lands in `<BASE_IMAGES_DOWNLOAD_DIR>/<net_id>/<central_hub_project_id>/<accession_id>/` with a `.flip_complete-<assessor>-<resource>` sentinel written after successful extraction (`services/image_cache.py`); a hit is checked with one XNAT resource-stats call and a changed resource re-downloaded (FLIP#953 — FL apps fetch the cohort per round; README, "Download"). Invalidated by uploads to the same (project, accession), by `force_refresh=true`, and wiped with the net dir by FLARE's `CleanupImages`; bounded by a TTL on last use (FLIP#1050): every hit refreshes the sentinel mtime and `services/cache_retention.py` sweeps hourly on the event loop, yielding only between studies, never in a thread (`IMAGE_CACHE_RETENTION_HOURS`, 0 = off)
- A client with no mount of the net folder (an NVFLARE job pod on Kubernetes, FLIP#1049) uses the stream twins: `POST /download/images/{net}/stream` answers with the cached study as a tar (`services/download.py::tar_study`), `PUT /upload/images/{net}/files` takes the files as multipart, each named by its path relative to the net's upload folder as on the mount route, and stages them at `<net>/upload/<request id>/<path>` for the upload. flip-utils switches on `IMAGES_TRANSPORT=stream`; the cache fill and the sealed project id are the same as the mount routes'
- The download URL pins `structure=default` (`<accession>/scans/<scan id>-<desc>/resources/<R>/files/…`), the layout FL apps read; never change it (README, "Download")

## Commands

```bash
make test        # ruff + mypy + pytest (unit only — no integration target)
make unit_test   # Unit tests only
```
