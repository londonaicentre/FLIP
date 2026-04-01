<!--
    Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at
        http://www.apache.org/licenses/LICENSE-2.0
    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.
-->

# Imaging API

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![FLIP Imaging API CI](https://github.com/londonaicentre/FLIP/actions/workflows/imaging_api.yml/badge.svg)](https://github.com/londonaicentre/FLIP/actions/workflows/imaging_api.yml)
[![imaging-api](https://ghcr-badge.egpl.dev/londonaicentre/imaging-api/latest_tag?trim=major&label=imaging-api)](https://github.com/londonaicentre/FLIP/pkgs/container/imaging-api)
[![Coverage](https://codecov.io/gh/londonaicentre/FLIP/branch/main/graph/badge.svg?flag=imaging-api)](https://codecov.io/gh/londonaicentre/FLIP)

The **imaging-api** is a Trust-side service that manages imaging data operations within the FLIP platform. It
interfaces with [XNAT](https://www.xnat.org/) for imaging project management and [Orthanc](https://www.orthanc-server.com/)
as the mock PACS source. It is called only by the [trust-api](../trust-api/).

Tested with `XNAT version 1.9.3, build: 158`.

## Role in the FLIP Platform

The imaging-api handles the imaging data lifecycle for federated learning studies:

1. **Project creation** — creates XNAT projects and user accounts for approved FL studies
2. **DICOM import** — queries Orthanc (PACS) with accession numbers and queues image retrieval into XNAT
3. **Data download** — packages and transfers XNAT datasets for FL training
4. **Upload** — stores result files back into XNAT experiments
5. **Retrieval status** — monitors import progress by querying the XNAT database

## Deployment

The imaging-api starts as part of the Trust-side stack:

```bash
make up-trusts
```

It requires both [XNAT](../xnat/) and [Orthanc](../orthanc/) to be running.

## API Reference

### Download

Download and unzip a XNAT dataset to a local folder.

```bash
net_id: net-1
{
  "encrypted_central_hub_project_id": "string",
  "accession_id": "string"
}
```

### Imaging

Interfaces with XNAT's DICOM Query-Retrieve (DQR) plugin. Full DQR API docs available at
`http://127.0.0.1:8104/xapi/swagger-ui.html#/dicom-query-retrieve-api`.

- Query PACS with an accession number
- Queue image retrieval from PACS to an XNAT project

Example DQR import request:

```json
{
  "pacsId": 1,
  "aeTitle": "XNAT",
  "port": 8104,
  "projectId": "b83294b0-4ff8-4629-bfb3-4cb587e87756",
  "forceImport": true,
  "studies": [{
    "studyInstanceUid": "1.2.826.0.1.3680043.8.274.1.1.8323329.1190647.1740750893.798613",
    "accessionNumber": "FAK77115197"
  }]
}
```

### Projects

Create an XNAT project from a Central Hub project:

```json
{
  "project_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "trust_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "project_name": "my_project",
  "query": "some query",
  "users": [
    {
      "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "email": "user@company.com",
      "is_disabled": false
    }
  ]
}
```

### Retrieval

Get import status or count for a project (queries the XNAT PostgreSQL database directly):

```
project_id: 8ba38209-97f5-41b9-976e-dfe3c5c8dd94
query: SELECT * FROM omop.radiology_occurrence
```

### Upload

Upload files to an XNAT experiment:

```json
{
  "encrypted_central_hub_project_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "accession_id": "FAK09131796",
  "scan_id": "12345",
  "resource_id": "RES",
  "files": [
    "FAK09131796/scans/1_2_826_.../resources/NIFTI/files/input.nii"
  ],
  "exist_ok": true
}
```

### Users

Create users, update user details, or add a user to an XNAT project.

## Configuration

Key environment variables (set in [`.env.development.example`](../../.env.development.example)):

| Variable | Description |
| --- | --- |
| `XNAT_URL` | URL of the XNAT instance |
| `XNAT_SERVICE_USER` | XNAT service account username |
| `XNAT_SERVICE_PASSWORD` | XNAT service account password |
| `XNAT_DATABASE_URL` | PostgreSQL connection string for the XNAT database |
| `DATA_ACCESS_API_URL` | Internal URL of the data-access-api |
| `AES_KEY_BASE64` | AES encryption key for decrypting project identifiers |

## Further Reading

- [XNAT setup](../xnat/README.md)
- [Orthanc setup](../orthanc/README.md)
- [Trust deployment overview](../README.md)
- [Contributing & Development Guide](CONTRIBUTING.md)
