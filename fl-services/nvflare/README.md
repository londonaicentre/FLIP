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

# NVFLARE Federated  Learning services

This folder contains base code to create NVIDIA FLARE federated learning networks, each containing a set of clients, a server and an API. `fl-api-base` and `fl-base` are base services used by the provisioning command to build upon. `fl-base` can be used to test applications locally, but is a single container that does not constitute a fully working FL network.

This diagram provides an overview of the services:

![FL Services Architecture](../assets/fl-services_overview.png)

## Images: built in CI, or locally as `:dev`

The four FL images — `flare-fl-base`, `flare-fl-server`, `flare-fl-client`, `flare-fl-api` — are built
in CI by [`fl-docker-build-nvflare.yml`](../../.github/workflows/fl-docker-build-nvflare.yml) whenever
`fl-services/nvflare/**` or `flip-utils/**` change, and published to GHCR as `:<sha>`, `:stag` (on `develop`)
and `:prod` (on `main`).
Deployments pull them via `DOCKER_FL_TAG`, so a change to fl-api code or the `flip` package flows into the
next deployment on that tag.

To iterate locally **before pushing**, build them tagged `:dev` from the repo root:

```bash
make build-fl                  # builds flare-fl-{base,server,client,api}:dev (base first, then derived)
make build-fl LOCAL_DEV=true   # include dev-only deps (e.g. plotting for the diffusion app)
```

Then run the stack on those local images instead of GHCR:

```bash
make up DOCKER_FL_REGISTRY= DOCKER_FL_TAG=dev
```

`DOCKER_FL_REGISTRY=` empties the registry so Docker resolves the local `flare-fl-*:dev` images (see
[`deploy/fl_backend.mk`](../../deploy/fl_backend.mk)). Note `make build` does **not** build these — the deploy
compose pulls FL images by tag, so their build definitions live only in [`compose.dev.yml`](./compose.dev.yml).

## Step-by-step provisioning

### Project yml file

The per-environment project files in [`../../deploy/providers/nvflare/`](../../deploy/providers/nvflare/)
(`net-1_project_dev.yml`, `net-2_project_dev.yml`, `net-1_project_stag.yml`, `net-1_project_prod.yml`)
define the services available within a network. Modify the relevant one if you want to:

- Incorporate further services (e.g. >2 clients)
- Modify GPU resources for the services
- Modify default ports [etc.]

### Net-specific yml file

You can run `make nvflare-provision NET_NUMBER=${NET_NUMBER}` to create a network: this will create an instance of the
services defined in `net-${NET_NUMBER}_project_dev.yml`, substituting the naming by `net-${NET_NUMBER}`.
You can also pass `FL_PORT` if you do not want to use the default (which will be the same for each created net).

### Provisioning command

This runs the `nvflare provision` CLI as part of `make nvflare-provision`. It is executed from the
`fl-services/nvflare/fl-api-base` uv project (which declares `nvflare`), so it resolves even though the repo-root `flip`
project has no dependencies. It creates the services defined in the net-specific yml file, initially under
`deploy/providers/nvflare/workspace/net-${NET_NUMBER}/prod_XX`, with default names.
Inside of these services, you should have at least a `local` and `startup` folder. The `startup` folder contains the
scripts to start and stop the services (`start.sh`, `stop_fl.sh` etc.), as well as configuration files
(`fed_[service_name].json`), and signature and certificate files.
Once these service files are created, the signature and certificate files will link them together and make them not
re-usable.

After this command is run, the make command moves every service into `deploy/providers/nvflare/workspace/net-${NET_NUMBER}/services/`.
Additionally, files that are not created by the `nvflare provision` command yet are crucial to run
the services (e.g. Python API files for the Admin API) will be added from `fl-base` (for client and server) and
`fl-api-base` (for API).

Once your network is provisioned, you can test it works by running

```sh
make up NET_NUMBER=<NET_NUMBER>
```

### Onboarding a new client onto an existing network

To onboard a new client, the procedure would involve:

- Run Python `nvflare provision` on the same network-specific yml file that was used to create the FL network, but
adding the new client on the yml file.
- Extract the client's folder from the workspace (deleting everything else), restructure it to match every other
client's and remove everything else that was created and left in the workspace (server, admin, other clients).
- Replace the `rootCA.pem` from the client's `startup` folder by the server's `fl-server/startup/rootCA.pem` of the
network the client belongs to.

This should ensure that the client stays part of the network, without re-creating the other services or altering signed
files.

There is a helper Makefile target for this process (wrapping `deploy/providers/nvflare/scripts/provision-additional-client.sh`):

```sh
make nvflare-provision-additional-client NET_NUMBER=<NET_NUMBER>
```
