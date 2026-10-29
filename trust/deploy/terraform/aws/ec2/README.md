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

# AWS EC2 trust node

> **Deploys: trust only** (FLIP#1528). A FLIP trust node on one EC2 instance, in any AWS account and
> VPC. It reads nothing from a hub: its own state, no hub env file, no hub credentials. The kit file and
> the registration are its only links to a hub.

Runbook: [Deploy a FLIP node on AWS EC2](../../../../../docs/source/deploy-flip/deploy-flip-node-aws-ec2.rst)
(network modes, egress, IAM, cost, commands).

| Path | What it is |
|---|---|
| `*.tf` | The root: the node's security group (no inbound), role, key pair and instance, plus the opt-ins in `optional.tf` (SSM endpoints, stop/start schedule) |
| `../modules/network/` | An existing subnet passed through, or a VPC with a NAT gateway; the EKS root (`../eks/`) uses it too |
| `Makefile` | `init plan apply ssh-config provision deploy upgrade …`; `make help` lists them |
| `scripts/ssh_config.py` | The `~/.ssh/config` entry that reaches the node over SSM |
| `architecture/` | The node diagrams, rendered by the docs build; `tests/test_architecture_diagram.py` fails when they drift from the `.tf` files |
| `tests/` | `terraform test` (mocked provider) and the Makefile / ssh-config pytest: `make test` |

The host is configured by [`../../../ansible/aws-ec2.yml`](../../../ansible/aws-ec2.yml), whose plays
(`aws-ec2-host.yml`) the hub's own trust EC2 shares until FLIP#1438 PR 3 removes it from the hub root.

```bash
make test lint      # credential-free; CI: validate_terraform.yml "AWS EC2 node checks"
```
