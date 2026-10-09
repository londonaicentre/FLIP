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

# FLIP trust node on Azure

A FLIP trust node on an Azure VM, built entirely from code (FLIP#1390). The VM sets itself up
at first boot and is operated only through Azure Run Command: no inbound ports, no SSH from
outside. The node receives its kit the way a real trust does: the operator packs it and drops it in
a private container the node reads with its own identity. This is the VM half of the Azure work;
the AKS half comes later.

## What this builds

| Layer | Directory | What it creates | Lifetime |
|---|---|---|---|
| Bootstrap | `bootstrap/` | Resource group, Terraform state storage (Entra ID access only), a budget alert, the node's static outbound IP | Created once, kept across node rebuilds |
| Node | `vm/` (+ `modules/network/`) | VNet, subnet and NSGs with no inbound rules, an Ubuntu 24.04 VM (Trusted Launch), a data disk for `/opt/flip`, a daily auto-shutdown | Created and destroyed per session |
| Kit drop | `modules/kit_drop/` (on by default in `vm/`) | A storage account with shared keys off and a private `kits` container. Its firewall admits only the node's subnet and your IP; every blob is deleted a day after upload. The node can read the container, you can write to it | With the node |

At first boot cloud-init installs `flip-node` (`vm/templates/flip-node.sh`). It mounts the data
disk, clones FLIP at the commit you planned with, and runs `trust/deploy/ansible/azure.yml` on
the VM itself. `make selftest-kit` packs an FL kit on your machine and puts it in the kit drop;
`make selftest` has the node fetch it and bring the trust stack up with no hub, then checks it.
The node never provisions an FL kit, so it carries no provisioning tooling.

## Before you start

- `brew install azure-cli`, then `az login`.
- Know your subscription's **name** (for example `Azure subscription 1`). Every target needs it
  as `AZ_SUBSCRIPTION`; nothing here uses the CLI's default subscription, which on a work
  account is often not yours.
- An SSH public key at `~/.ssh/id_ed25519.pub` (or set `SSH_PUBLIC_KEY_FILE`). Azure requires
  one for Linux VMs; nothing listens for SSH from outside.
- The commit you deploy must be pushed to GitHub, because the VM clones it.
- `yq` and `uv` on your machine, for `make selftest-kit` (it provisions the FL dev kit here).
- `plan` looks up your public IP (api.ipify.org) for the kit drop's firewall; override it with
  `OPERATOR_IP=<ip>`. If your IP changes, `kit-upload` is refused until you plan and apply again.

**Cost.** Nothing here skips Terraform's confirmation: every `bootstrap`, `apply` and `destroy`
shows the plan and asks. On a Free Trial with the spending limit on, running out of credit
stops resources rather than billing you. A running `Standard_D4s_v5` uses roughly $0.20 an
hour; `make stop` deallocates it so only the disks and the IP cost anything; `make destroy`
at the end of a session removes the node and keeps the bootstrap layer.

## Steps

```bash
cd deploy/providers/azure
SUB="Azure subscription 1"

make bootstrap AZ_SUBSCRIPTION="$SUB" STATE_SA=flipaztfstate<random> ALERT_EMAIL=<you>
make init      AZ_SUBSCRIPTION="$SUB"
make plan      AZ_SUBSCRIPTION="$SUB"
make apply     AZ_SUBSCRIPTION="$SUB"
make status    AZ_SUBSCRIPTION="$SUB"      # repeat until "provisioned: <time>"
make selftest-kit AZ_SUBSCRIPTION="$SUB" FL_BACKEND=nvflare   # pack here, upload to the kit drop
make selftest  AZ_SUBSCRIPTION="$SUB" FL_BACKEND=nvflare
make logs      AZ_SUBSCRIPTION="$SUB" UNIT=flip-selftest-nvflare
make report    AZ_SUBSCRIPTION="$SUB" FL_BACKEND=nvflare
make destroy   AZ_SUBSCRIPTION="$SUB"
```

Repeat `selftest-kit` / `selftest` / `logs` / `report` with `FL_BACKEND=flower`. `make help`
lists every target.

## Joining a hub

The hub admin registers the trust and packages its kit exactly as for an on-prem trust
(`new-trust`, `make -C deploy/providers/AWS register-trusts`, `sync-trust-kit`,
`package-onprem-trust-kit`; see `docs/source/deploy-flip/deploy-flip-node-azure.rst`), then:

```bash
make kit-upload AZ_SUBSCRIPTION="$SUB" TARBALL=<flip-trust-kit-…tar.gz>
make join       AZ_SUBSCRIPTION="$SUB" NAME=<that file name> KIT=<kit name> PROD=<env>
make logs       AZ_SUBSCRIPTION="$SUB" UNIT=flip-join-<kit name>
```

`join` fetches the kit with the node's identity, installs its kit file owner-only with the
node's own settings appended, stages the FL kit for the client that reads it and runs
`up-onprem-trust`, whose checklist gates the start. The node's FL client dials the hub's FL
edge itself (outbound); nothing listens on the node.

## Choosing a region

`LOCATION` (default `uksouth`) is the node's region, and the outbound IP follows it; the
bootstrap's state storage stays in `BOOTSTRAP_LOCATION` (default `uksouth`), so moving the node
replaces only the IP. Free Trial subscriptions often cannot create 4-vCPU VMs in the busy UK
regions (`SkuNotAvailable` / `NotAvailableForSubscription`); check what yours allows with
`az vm list-skus --subscription "$SUB" --location <region> --size Standard_D4 --all -o table`,
then pass the same `LOCATION=<region>` to `bootstrap` (to move the IP) and to `plan`.

## When something goes wrong

- **The budget alert is rejected.** Some trial offers do not support budgets. Rerun
  `make bootstrap` without `ALERT_EMAIL` (it sets `create_budget=false`); the spending limit
  remains the guard.
- **First boot fails.** `make status` shows cloud-init's state; `make logs UNIT=cloud-final`
  shows its output; the portal's boot diagnostics show the serial console. Fix the cause in
  the repo, push, then `make reprovision REF=<new sha>`.
- **The data disk never appears.** `flip-node` waits five minutes for it and then stops
  rather than writing `/opt/flip` to the OS disk. Check the disk attachment, then
  `make reprovision`.
- **A self-test check fails.** The report names the check. Fix it in the repo and
  reprovision; do not edit the node by hand.
- **The node cannot fetch its kit.** `fetch-kit` retries for about five minutes and then
  names the status: 403 right after an apply is the node's read role still propagating (run it
  again); 404 means the blob was never uploaded or has expired (kits last a day: upload again).

## Checks without Azure

```bash
make test   # terraform test (mocked azurerm) in every root and module, plus the static guards
make lint   # terraform fmt and tflint with the azurerm ruleset
```

CI runs both in `validate_terraform.yml`. The static guards (`tests/test_static_guards.py`)
enforce the cost and safety rules: nothing skips Terraform's confirmation, every provider and `az` call names the
subscription, no real subscription IDs in tracked files, no inbound NSG rules, and the VM's
NIC always behind its own NSG.
