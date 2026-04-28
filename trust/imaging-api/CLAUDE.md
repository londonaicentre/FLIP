# CLAUDE.md — imaging-api (DICOM Retrieval)

## Service Overview

FastAPI service for DICOM image retrieval from PACS (Orthanc/XNAT). Receives requests from trust-api, queries PACS, retrieves images, and returns results.

## Key Patterns

- Communicates with Orthanc (port 4242) and XNAT (port 8104) on the trust network
- Only receives internal requests from trust-api (not directly exposed)
- DICOM-to-NIfTI conversion support

## Commands

```bash
make test        # ruff + mypy + pytest (unit + integration)
make unit_test   # Unit tests only
```
