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

# NVFLARE Federated  Learning services

This folder contains base code to create NVIDIA FLARE federated learning networks, each containing a set of clients, a server and an API. `fl-api-base` and `fl-base` are base services used by the provisioning command to build up on. `fl-base` can be used to test applications locally, but is a single container that does not constitute a fully working FL network.

This diagram provides an overview of the services:

![FL Services Architecture](../assets/fl_services_overview.png)

## Step-by-step provisioning

### Project yml file

The `net_project.yml` file is a template that will define the services available within the network. Modify this file if
you want to:

- Incorporate further services (e.g. >2 clients)
- Modify GPU resources for the services
- Modify default ports [etc.]

### Net-specific yml file

You can run `make nvflare-provision NET_NUMBER=${NET_NUMBER}` to create a network: this will create an instance of the
services defined in `net_project.yml`, substituting the naming by `net-${NET_NUMBER}`.
You can also pass `FL_PORT` and `ADMIN_PORT` if you do not want to use the defaults (which will be the same for each
created net).

### Provisioning command

This will run the `nvflare provision` Python command. It is part of the `make nvflare-provision` command, and will
create the services that are defined in the net-specific yml file.
It will initially create these services in the `workspace/prod_XX` folder, with default names.
Inside of these services, you should have at least a `local` and `startup` folder. The `startup` folder contains the
scripts to start and stop the services (`start.sh`, `stop_fl.sh` etc.), as well as configuration files
(`fed_[service_name].json`), and signature and certificate files.
Once these service files are created, the signature and certificate files will link them together and make them not
re-usable.

After this command is run, the make command will take care of moving every service into the `fl_services` folder, under
`net-${NET_NUMBER}`. Additionally, files that are not created by the `nvflare provision` command yet are crucial to run
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

There is a helper Makefile target to help with this process:

```sh
bash provision_client.sh <NET_NUMBER>
```