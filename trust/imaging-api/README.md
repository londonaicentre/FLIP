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

Reimplementation of flip-trust-imaging-api in Python. Allows creation of XNAT projects, users, downloading data, and querying from Orthanc (mock PACS).

Tested with `XNAT version 1.9.1.1, build: 158` (see xnat folder in the repo).

Implemented services so far:

### Download

Download and unzip XNAT dataset to local folder

Example API request:

```
net_id: net-1
{
  "encrypted_central_hub_project_id": "string",
  "accession_id": "string"
}
```

### Imaging

*See official XNAT DQR API documentation under <http://127.0.0.1:8104/xapi/swagger-ui.html#/dicom-query-retrieve-api>*

* Query PACS with an accession number
* Queue image retrieval from PACS to an XNAT project

DQR import request example input:

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

Project creation in XNAT from the following Central Hub project information.

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

Get import status / import status count of a project. Interacts with XNAT Postgres database directly.

```
project_id: 8ba38209-97f5-41b9-976e-dfe3c5c8dd94
query: SELECT * FROM omop.radiology_occurrence
```

### Upload

Upload files to XNAT experiment

Example API request:

```json
{
  "encrypted_central_hub_project_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "accession_id": "FAK09131796",
  "scan_id": "12345",
  "resource_id": "RES",
  "files": [
    "FAK09131796/scans/1_2_826_0_1_3680043_8_274_1_1_8323329_1190295_1740750886_121053-CT_Spleen/resources/NIFTI/files/input_CT_Spleen_20171029204735_1724827370.nii",
    "FAK09131796/scans/1_2_826_0_1_3680043_8_274_1_1_8323329_1190295_1740750886_121053-CT_Spleen/resources/NIFTI/files/input_CT_Spleen_20171029204735_1724827370.nii"
  ],
  "exist_ok": true
}
```

### Users

User creation, update, add user to project,

## Run

You'll need to have started XNAT (see [xnat](../xnat/)) and Orthanc (see [orthanc](../orthanc/)) to be able to run the endpoints.

To run locally, you need a `uv` installation. The command below will load the environment variables correctly:

```sh
make dev
```

To run in Docker, use

```sh
make up
```

## Tests

Note tests need a running XNAT instance with test data. The test data (accession numbers, paths, etc) are currently hardcoded.
