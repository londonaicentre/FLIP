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

# Federated Learning FL API

This is the base FL API service. It is used to create instances of the FLIP federated learning API.

The FL API for FLARE wraps the NVIDIA Flare `Session` object. In particular, we created a child of `Session` called `FLIP_Session` that wraps some of the `Session` function calls. There is minimal functionality difference between `Session` and `FLIP_Session`; these two classes consist of NVFLARE code, while `FL-API` itself is a Fast-API instance. The API itself interacts with the Central Hub for job monitoring, job submission and check-up of status of federated components (clients and server).

## Ports

Port 8000 is normally used by the FL API.

## Overview of FL API endpoints

Some of the endpoints in the API do not call `Session` and are therefore not technically dependent of an NVFLARE object. In addition, whereas the FL API wraps some of the functionalities available in `Session` instances, they do not interact with any component other than the FL API and are merely there for debugging or interact with the FL services directly from the FL-API. 

On the other hand, when a job is launched, the `FLIP` package functions (especially `flip.py`, which instances a class called `Flip`) communicate with the Central Hub **directly**.

This diagram is an overview of all endpoints available from the FL-API and the `FLIP` package itself. 

![image.png](../../assets/fl_api_endpoints.png)

### List of endpoints

1) **FL-API endpoints that do NOT call `Session`**

#### health

Method type: GET

Parameters: **None**

Returns: the response body contains “status”: state of the server
_Who calls it_: Central Hub API

#### upload_app

Although it does not call FLARE, this function configures a FLARE-dedicated app (with its configure_fed_server, configure_fed_client etc.) This is the method that uploads a ready-to-launch FLARE application. 

Method type: POST

Parameters: 

- `Model ID`: UUID model ID which will be saved on the CentralHub database, which will be added to the config_fed_X.json files
- `Body`: includes:
    - `Global rounds`: Number of server rounds
    - `Local rounds`: Number of rounds per site before aggregation
    - `Bundle URLS`: URLS to the files that will be uploaded from the APP upload bucket into the application folder (app folder)
    - `Project ID`: ID of the Central Hub project for this app
    - `Cohort query`: SQL query linked to this project
    - `Trusts`: list of participating trusts
    - `Ignore_result_error`: bool
    - `Aggregator`: type of aggregator that will be used (FLARE aggregator name)
    - `Aggregation weights`: {trust: weight} argument that will be passed to the aggregation during construction (will be added to config)
- `upload_dir`: path where the apps are saved

_Who calls it_: Central Hub API (upload_app in fl_service)

2) **FL-API endpoints that call `Session`**

Calls FLARE's `session.submit_job`. Kicks the job. 

Method type: POST

Parameters:

- `App folder` (that has been previously configured in the FL-API). This folder is inside of FL-API’s upload_dir, and will send it over to the server and client.

_Who calls it_: Central Hub API (upload_app in fl_service)

#### list_jobs

This lists the NVFLARE jobs available on the server (including those that have failed). 

Method type: GET

Parameters:

- `detailed`: extensive description required 
- `limit`: maximum number of jobs to list
- `id_prefix`: prefix for the job ID (string)
- `name_prefix`: prefix for the job name search (string)
- `reverse`: order reverse to submission time 

Returns: a list of dictionaries (with elements related to the job such as name, job_id, status etc.).

_Who calls it_: Central Hub (extract_current_job_data in fl_service), without arguments (it then filters by job_id). It’s only used to abort jobs.


#### abort_job

Method type: DELETE

Parameters:
- `job_id`

_Who calls it_: Central Hub API (abort_job in fl_service)

#### check_status

It provides the status of the Central Hub FL server, FL clients, or both, depending on the parameters.

Method type: GET

Parameters:
- `target_type`: type of target (client or server)
- `targets`: list of specific targets

_Who calls it_: Central Hub API in fl_services


#### check_server_status

It provides the status of the Central Hub FL server.

Method type: GET

_Who calls it_: Central Hub API in fl_services


#### check_client_status

It provides the status of the FL clients.

Method type: GET

Parameters:
- `targets`: list of specific targets (e.g. client-1)

_Who calls it_: Central Hub API in fl_services


3) **FL-API endpoints that are not used in FLIP**

#### show_errors

Shows errors for a specific job ID and, optionally, a list of clients.

Method type: POST

Parameters:
- `job_id`: Job ID to show errors for
- `target_type`: (e.g. server, client, all)
- `targets`: (list of targets - e.g. server, client 1 etc.)


#### show_stats

Shows statistics (status, ID etc.) for a specific job ID and, optionally, a list of clients.

Method type: POST

Parameters:
- `job_id`: Job ID to show stats for
- `target_type`: (e.g. server, client, all)
- `targets`: (list of targets - e.g. server, client 1 etc.)


#### reset_errors

Resets errors for a specific job ID.

Method type: POST

Parameters:
- `job_id`: Job ID to reset errors for


#### delete_job

Deletes a job from the system.

Method type: DELETE

Parameters:
- `job_id`: Job ID to delete


#### get_available_apps_to_upload

Lists available apps in the Session’s upload_dir.

Method type: GET

Parameters: **None**


#### download_job_result

Downloads model results (not used, as FLIP does this via the Central Hub and AWS using the model ID).

Method type: GET

Parameters:
- `job_id`: NVFLARE job ID


#### get_connected_client_list

Gets a list of connected clients.

Method type: GET

Parameters: **None**


#### get_working_directory

Gets the working directory on the targets (runs pwd on the clients or server).

Method type: GET

Parameters:
- `target`: target where you want to run this


#### restart

Restarts the specified targets.

Method type: POST

Parameters:
- `target_type`: targets that you want to restart
- `client_names`: for target_type client, this is a list of specific clients you want to restart

#### shutdown

Shuts down the specified targets.

Method type: POST

Parameters:
- `target_type`: targets that you want to shut down (e.g. server, client, all)
- `client_names`: for target_type client, this is a list of specific clients you want to shut down

#### shutdown_system

Shuts down the system.

Method type: POST

Parameters: **None**

## Testing

The nvflare `local` and `startup` folders are created during the provisioning of each real network (server, client(s) and API). However, Python tests require `fed_admin.json` to exist within `admin/startup`. Therefore, this file is created dynamically in [./tests/utils/test_flip_session.py](./tests/utils/test_flip_session.py) for testing purposes.

`FL_ADMIN_DIRECTORY` is set to a temporary directory in [./tests/conftest.py](./tests/conftest.py) during testing to avoid conflicts with any existing admin directories.
