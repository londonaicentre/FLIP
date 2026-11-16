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

The FLARE server typically uses two ports. Both are configurable in the project configuration file.

| Port | Purpose |
|------|---------|
| 8002 | Clients (training sites or edge devices) connect over gRPC to send and receive job data. |
| 8003 | Communication with the Admin API, which manages the system and job scheduling. |

From FLARE 2.7 onwards, [one port can serve both controller and admin API traffic](https://nvflare.readthedocs.io/en/2.9.0/user_guide/admin_guide/configurations/server_port_consolidation.html).

## Configuration

The FL server needs to have the same python packages installed as the FL client, because it will try to instantiate the model and aggregate weights.

The server config includes a PyTorch model persistor:

```json
{
  "id": "persistor",
  "path": "nvflare.app_common.pt.pt_file_model_persistor.PTFileModelPersistor",
  "args": { "model": { "path": "models.get_model" } }
}
```

## FLIP site components

At start-up the entrypoint runs:

```bash
python -m flip.nvflare.server_site_config <workspace>/local
```

The workspace is `/app` by default, or `FL_WORKSPACE` if set (`/workspace` on ECS). The command writes `local/flip__p_resources.json`. If the file cannot be written, the container refuses to start.

That file registers `JobSchedulingReporter` (FLIP#1390):

- When FLARE's scheduler holds a job because a Trust cannot provide the GPUs its `resource_spec` asks for, the reporter records this in the model's activity feed.
- On the scheduler's last try, it marks the model `ERROR`.

FLARE merges every `local/*__p_resources.json` into the config of the server's parent process, which runs the job scheduler. It never merges them into a job, so the provisioned `resources.json` stays untouched. See
[GPU requirements and FLARE's scheduler](../../../docs/source/components/component-fl-nets.rst).

## Rolling back to an fl-server image older than FLIP#1390

Delete `local/flip__p_resources.json` before you start an older image. `local/` is the kit mount, so the file outlives the container. An older image still loads the file, cannot import the `JobSchedulingReporter` it names, and fails while loading its configuration.

Where the file lives:

- **Compose host, production:** `/opt/flip/services/fl-server-net-<N>/local/`
- **Compose host, development:** `$FL_PROVISIONED_DIR/net-<N>/services/fl-server-net-<N>/local/`
- **ECS:** `local/` is an EFS volume. `make rollback-centralhub` (in `deploy/providers/AWS`) handles it for you. Before repointing any service, it runs the one-shot `efs-provision-certs` task that Terraform runs when the kit changes. That task empties `local/` and re-syncs the kit from S3 (`scripts/reset-fl-server-local.sh`).

To do the ECS step by hand, for instance before rolling back with plain `aws ecs update-service`:

```bash
NET=$(aws ecs describe-services --cluster flip-cluster --services fl-server-net-1 \
  --query 'services[0].networkConfiguration' --output json)
aws ecs run-task --cluster flip-cluster --task-definition efs-provision-certs \
  --launch-type FARGATE --network-configuration "$NET"
```

The running fl-server has already loaded the file, and a current image rewrites it at every start, so
rolling forward again needs nothing.
