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

# Federated Learning Server

Code to deploy the NVIDIA FLARE Federated Learning server.

## Ports

> Note that from version 2.7 onwards, [there is an option to use a single port for both controller and admin API communications](https://nvflare.readthedocs.io/en/2.7.0/user_guide/admin_guide/configurations/server_port_consolidation.html).

In NVIDIA FLARE, ports 8002 and 8003 are typically used by the FL server and associated services to facilitate communication between components of the federated learning system.

🔌 **Port 8002**: The FLARE server process listens on this port for messages from clients (e.g., training sites or edge devices). Clients (e.g., training sites) connect to this port using gRPC to send/receive job data.

🔌 **Port 8003**: The FLARE server uses this port to communicate with the Admin API, which manages the overall system and job scheduling.
These ports are configurable in the project configuration file. 

## Configuration

The FL server needs to have the same python packages installed as the FL client, because it will try to instantiate the model and aggregate weights.

The server config includes a PyTorch model persistor:

```json
"id": "persistor",
"path": "nvflare.app_common.pt.pt_file_model_persistor.PTFileModelPersistor",
"args": { "model": { "path": "models.get_model" } }
```
