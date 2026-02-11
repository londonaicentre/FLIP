<!--
    Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

# Federated Learning FL API

This is the base FL API service. It is used to create instances of the FLIP federated learning API.

The FL API contains a `Session` object. The API itself interacts with the Central Hub for job monitoring, job submission and check-up of status of federated components (clients and server).

## Overview of FL API endpoints

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `health` | `GET` | Health check for the FL API |
| `upload_app` | `POST` | Configure and upload a Flower app bundle |
| `submit_job` | `POST` | Submit and start a Flower federated job |
| `list_jobs` | `GET` | List Flower jobs on the server (including failed jobs) |
| `abort_job` | `DELETE` | Abort a job by `job_id` |
| `check_server_status` | `GET` | Return Central Hub FL server status |
| `check_client_status` | `GET` | Return client and Central Hub node status |

### `health`
- Method: `GET`
- Parameters: `None`
- Called by: Central Hub API

### `upload_app`
Configures a Flower app bundle.

- Method: `POST`
- Parameters:
  - `model_id`: FLIP model ID
  - `body`:
    - `bundle_urls`: URLs to app files uploaded from the app upload bucket into the app folder
    - `project_id`: Central Hub project ID
    - `cohort_query`: SQL query linked to the project
    - `trusts`: participating trusts list
  - `upload_dir`: path where apps are saved
- Called by: Central Hub API (`upload_app` in `fl_service`)

### `submit_job`
Submits the job.

- Method: `POST`
- Parameters:
  - `app_folder`: previously configured app folder inside FL API `upload_dir`; this folder is sent to server and clients
- Called by: Central Hub API (`upload_app` in `fl_service`)

### `list_jobs`
Lists Flower jobs available on the server, including failed jobs.

- Method: `GET`
- Parameters: `None`
- Returns: list of dictionaries containing job metadata (for example `name`, `job_id`, `status`)
- Called by: Central Hub API (`extract_current_job_data` in `fl_service`); used to locate jobs for abort workflows

### `abort_job`
- Method: `DELETE`
- Parameters:
  - `job_id`
- Called by: Central Hub API (`abort_job` in `fl_service`)

### `check_server_status`
Provides status of the Central Hub FL server.

- Method: `GET`
- Called by: Central Hub API (`check_status` in `fl_service` and AWS)

### `check_client_status`
Provides status of clients and Central Hub nodes.

- Method: `GET`
- Parameters:
  - `targets`: list of specific targets (for example `client-1`)
- Called by: Central Hub API (`check_status` in `fl_service` and AWS)
