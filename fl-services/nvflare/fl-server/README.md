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

# Federated Learning Server

Code to deploy the NVIDIA FLARE Federated Learning server.

## Ports

> Note that from version 2.7 onwards, [there is an option to use a single port for both controller and admin API communications](https://nvflare.readthedocs.io/en/2.9.0/user_guide/admin_guide/configurations/server_port_consolidation.html).

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

## FLIP site components

At start-up the entrypoint runs `python -m flip.nvflare.server_site_config /app/local`, which writes
`local/flip__p_resources.json`. NVFLARE merges every `local/*__p_resources.json` into the server's
parent process config, the process that runs the job scheduler, and never into a job, so the
provisioned `resources.json` stays untouched. It registers `JobSchedulingReporter`: when NVFLARE's
scheduler holds a job because a Trust cannot provide the GPUs its `resource_spec` asks for, the
reporter puts that in the model's activity feed, and on the scheduler's last try it marks the model
`ERROR` (FLIP#1390). The container refuses to start if the file cannot be written. See
[GPU requirements and NVFLARE's scheduler](../../../docs/source/components/component-fl-nets.rst).

**Rolling back past this change takes one step first.** `local/` is the kit mount, so the file outlives
the container. NVFLARE in an fl-server image older than FLIP#1390 still loads it, cannot import the
`JobSchedulingReporter` it names, and fails while loading its configuration. Delete
`local/flip__p_resources.json` before starting an older image. On a compose host it is in the mounted kit:
`/opt/flip/services/fl-server-net-<N>/local/` in production,
`$FL_PROVISIONED_DIR/net-<N>/services/fl-server-net-<N>/local/` in development. On ECS `local/` is an EFS
volume, and `make rollback-centralhub` (in `deploy/providers/AWS`) takes care of it: before it repoints any
service it runs the one-shot task Terraform runs when the kit changes, which empties `local/` and re-syncs
the kit from S3 (`scripts/reset-fl-server-local.sh`). To do the same by hand, for instance before rolling
back with plain `aws ecs update-service`:

```bash
NET=$(aws ecs describe-services --cluster flip-cluster --services fl-server-net-1 \
  --query 'services[0].networkConfiguration' --output json)
aws ecs run-task --cluster flip-cluster --task-definition efs-provision-certs \
  --launch-type FARGATE --network-configuration "$NET"
```

The running fl-server has already loaded the file, and a current image rewrites it at every start, so
rolling forward again needs nothing.
