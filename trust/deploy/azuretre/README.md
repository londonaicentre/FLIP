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

# FLIP trust node: AzureTRE workspace service

**Draft: not yet deployed on an AzureTRE.** Written against AzureTRE v0.29.1 (FLIP#1792). Expect the
first deployment, on a TRE operator's development TRE, to change it.

An AzureTRE workspace service template (a Porter bundle) that runs a FLIP trust node inside a
workspace. FLIP's guidance for TREs (loading the project's data, OMOP and Orthanc inside the node,
what leaves the TRE) is on
[Deploy a FLIP node in a TRE](https://londonaicentreflip.readthedocs.io/en/latest/deploy-flip/deploy-flip-node-in-tre.html).

## What it creates

- **A subnet of its own** in the workspace VNet, from an address range the TRE API allocates (a /24;
  no one deploying the service can choose it), on the workspace's route table, so everything leaves
  through the TRE firewall.
- **A network security group** on that subnet. Nothing inbound. Outbound: 443 to the internet and to
  the kit drop's private endpoint in the same subnet, 80 to the internet (OS packages), and the FL
  port to the FL server's IP addresses. The firewall rules below narrow 443 and 80 to named hosts.
- **An Ubuntu 24.04 VM** (`flipnode<id>`): Trusted Launch, encryption at host, a system-assigned
  identity, no public IP, and a 128 GB data disk for `/opt/flip`. An optional daily shutdown is off by
  default: a node that is off misses the hub's tasks.
- **A kit drop**: a storage account of the service's own (`stfk<tre><ws><id>`) with shared keys and
  public network access off, a private endpoint in the node's subnet registered in the TRE's
  `privatelink.blob.core.windows.net` zone, and a rule deleting kits a day after upload. Only the VM's
  identity is granted a data role (Storage Blob Data Reader).
- **Firewall rules** in the TRE firewall, added and removed by the template's `pipeline`.

At first boot cloud-init clones FLIP at the chosen release, installs the `flip-node` helper that
release ships (`trust/deploy/terraform/azure/vm/templates/flip-node.sh`, the one FLIP's own Azure VM
uses) and runs `flip-node provision`. That mounts the data disk, clones FLIP into `/opt/flip` and runs
`trust/deploy/ansible/azure.yml`. The bundle carries no copy of `flip-node`, so the node always runs
the helper of the release it installed. FLIP v0.14.0 is the first release with it.

## Build and register

1. Copy this directory into an AzureTRE checkout as `templates/workspace_services/flip-node`.
2. From the checkout's root, build, publish and register it:

   ```bash
   make workspace_service_bundle BUNDLE=flip-node
   ```

   That is `make bundle-build`, `bundle-publish` and `bundle-register BUNDLE_TYPE=workspace_service`
   (AzureTRE's *Registering templates* guide). `bundle-build` runs `terraform validate` and checks
   `parameters.json` against `porter.yaml`.
3. A workspace owner adds the service to a workspace from the TRE portal.

The checks in this directory need no Azure account:

```bash
make -C trust/deploy/azuretre test lint
```

## Parameters

| Property | Default | Notes |
|----------|---------|-------|
| `flip_release` | (required) | A release tag, `vX.Y.Z`, v0.14.0 or later. Use the release your hub runs. Read at first boot only: later, `flip-node reprovision vX.Y.Z` moves the code. |
| `fl_backend` | `nvflare` | `nvflare` or `flower`, as the hub runs. |
| `hub_api_hostname` | `app.flip.aicentre.co.uk` | FLIP's production hub, or `stag.flip.aicentre.co.uk`: the host of the hub URL in your kit. |
| `fl_server_ip_addresses` | (required) | One to four public IPv4 addresses of the hub's FL endpoint, from the FLIP team. Private, loopback, link-local and unspecified addresses are refused. |
| `fl_port` | `8002` | Your kit's FL port: 8002 on production, 8003 on staging. |
| `vm_size` | 4 CPU, 16 GB | `Standard_D4s_v5`, `D8s_v5` or `D16s_v5`. CPU only. |
| `enable_shutdown_schedule` | off | With `shutdown_time` (HHmm) and `shutdown_timezone`. |

The hub, FL addresses and FL port set what the TRE firewall lets out, so they are limited to FLIP's own
and fixed once deployed: changing one means a TRE admin removing the service and adding it again. The
subnet's range is always the TRE API's choice: the template declares no `address_space_size`, so the
API never accepts a `custom` range, and it replaces any `address_space` given.

## Getting the kit to the node

The hub admin sends the trust a kit tarball, as for any trust. It holds the trust's API key, the AES
key and the FL client's private key, which no researcher may read. So a **TRE admin** puts it in the
kit drop; it never goes through the airlock or a researcher's VM, and never into the workspace
storage account (AzureTRE's Guacamole VMs carry that account's key to mount its file share). The node
fetches it with its own identity when you run `flip-node join`.

The kit drop has no public endpoint, so upload from a host on the TRE network, such as AzureTRE's
**Admin VM** shared service (`tre-shared-service-admin-vm`, in the core network, reached through
Bastion):

1. Grant yourself the upload role on the container, then upload with the `kit_upload_command` output:

   ```bash
   az role assignment create --role "Storage Blob Data Contributor" --assignee <your object id> \
     --scope <kit storage account id>/blobServices/default/containers/kits
   az storage blob upload --auth-mode login --blob-url <kit_drop_url>/<kit>.tar.gz --file <kit>.tar.gz
   ```

2. Join (below). The kit is deleted within about 48 hours of upload; remove your role assignment when
   done.

> **TODO (first deployment):** confirm the upload route on a real TRE: that the Admin VM resolves the
> kit drop to its private endpoint (the core network is linked to the same DNS zone) and reaches it
> across the workspace peering.

## Running the node

The node takes no inbound connection: it is driven with Azure Run Command, as FLIP's Azure VM is. The
`status_command` and `join_command` outputs are ready to run (`HUB` is `stag` or `prod`):

```bash
az vm run-command invoke --ids <vm id> --command-id RunShellScript --query 'value[0].message' -o tsv \
  --scripts 'flip-node join <kit>.tar.gz <KIT> <HUB>'
```

The other subcommands are `flip-node status`, `selftest nvflare|flower`, `logs <unit>`,
`report nvflare|flower` and `reprovision vX.Y.Z`. The hubless self-test needs a self-test kit in the
kit drop, named `selftest-<backend>.tar.gz`, packed on your machine with
`bash trust/deploy/terraform/azure/scripts/pack-selftest-kit.sh <backend> selftest-<backend>.tar.gz`.

## Firewall rules it adds

Every rule's source is the node's subnet only.

| Rule | Destination | Why |
|------|-------------|-----|
| `flip-hub-api` | `hub_api_hostname`, 443 | Task polling, heartbeats and results |
| `flip-fl-server` (network rule) | `fl_server_ip_addresses`, `fl_port` | Federated learning, mutual TLS. By address: the TRE firewall runs no DNS proxy, so network rules cannot use names. Keep it out of any TLS inspection |
| `flip-images` | `ghcr.io`, `pkg-containers.githubusercontent.com` | FLIP's images. The TRE's Nexus mirror does not proxy GHCR |
| `docker-hub` | `registry-1.docker.io`, `auth.docker.io`, `production.cloudfront.docker.com` | The logging stack and XNAT's Docker socket proxy |
| `docker-engine` | `download.docker.com` | Docker Engine |
| `sample-data` | `huggingface.co`, `*.hf.co`, `pypi.org`, `files.pythonhosted.org` | FLIP's sample data, loaded by the first install |
| `flip-provisioning` | `github.com`, `codeload.github.com`, `galaxy.ansible.com`, `astral.sh`, `releases.astral.sh` | Cloning FLIP, the Docker Ansible role, uv |
| `ubuntu-packages` | `azure.archive.ubuntu.com`, `archive.ubuntu.com`, `security.ubuntu.com`, port 80 | OS packages and updates |

The hosts are FLIP's egress table (*Network requirements* on *Deploy a trust node*) plus its Azure
VM's first-boot hosts; `tests/` fails if a 443 host in either is missing here. `codeload.github.com`,
`galaxy.ansible.com` and the Ubuntu mirrors are not in those lists and have not been measured behind
a firewall yet. If the workspace has AzureTRE's **DNS Security Policy** on, add these hosts and the FL
endpoint's hostname to the TRE's `allowed_dns` too.

The rules are added **before** the VM is created, because its first boot clones FLIP from GitHub.

## The airlock decision

The airlock is AzureTRE's route for data in and out, with human approval. **Model updates over the FL
port do not pass through it**: the FL client sends them straight to the FL server through the
`flip-fl-server` rule. Agree that with the TRE's output-checking policy before deploying (FLIP's
position is under *What leaves the TRE, and under what policy* on the TRE page). Federated evaluation
returns metrics only and is the easier start; federated training releases a model update every
round. Removing the service removes the rule and stops all FL traffic.

## Not covered yet

- **GPUs**: `flip-node join` configures no GPUs, so training runs on CPU.
- **CI**: these checks are not yet run by any workflow.
- **Customer-managed keys** (`enable_cmk_encryption` in AzureTRE's templates): disks use
  platform-managed keys.
