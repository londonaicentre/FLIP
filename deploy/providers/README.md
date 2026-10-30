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

# Deployment Providers

A **provider** is the Terraform (and host bootstrap) that provisions cloud infrastructure for a FLIP component.
It does not define the container stack or configure a site-owned host — both live under
[`trust/deploy/`](../../trust/deploy/README.md), by the rule in [`../README.md#where-things-live`](../README.md#where-things-live).

| Provider | Provisions | Entry point |
| -------- | ---------- | ----------- |
| [`AWS/`](AWS/README.md) | **Hub only** — ECS Fargate, RDS + Proxy, NLB, CloudFront, Cognito, SES and the SSM bastion. It creates no trust (FLIP#1438): an AWS trust node is its own root, [`trust/deploy/terraform/aws/`](../../trust/deploy/terraform/aws/) | `make -C deploy/providers/AWS full-deploy HUB=<stag\|prod>` |

## How a trust node is deployed: shape × infrastructure

A trust node is one container stack in two shapes, and either shape can run on infrastructure a provider
created or on infrastructure the site already has. Keep the two axes apart when reading the tree:

| Node shape | Defined in | Installed by | Infrastructure it runs on |
| ---------- | ---------- | ------------ | ------------------------- |
| **Compose on a host** | [`trust/deploy/compose/compose_trust.*.yml`](../../trust/deploy/README.md) | [`trust/deploy/ansible/onprem.yml`](../../trust/deploy/ansible/README.md) (site-owned host), `aws-ec2.yml` or `azure.yml`, then `make up-onprem-trust` / the root's `make deploy` | an on-prem Ubuntu box, or a VM from `trust/deploy/terraform/{aws/ec2,azure}` |
| **Helm on Kubernetes** | [`trust/deploy/helm/`](../../trust/deploy/helm/README.md) (chart `flip-trust`) | `make -C trust/deploy/helm deploy-trust-k8s KIT=<CODE> HUB=<stag\|prod>` | a site-managed cluster (k3s, on-prem) or a managed one (AKS, EKS) |

**Spell `HUB` out** on either row: `deploy/providers/AWS/Makefile` refuses to run without it, while
`trust/deploy/helm/Makefile` reads **the `development` kit when `HUB` is unset**.

The **Central Hub** has exactly one supported production target, AWS ECS Fargate (hub-on-EC2 was deprecated in
[#936](https://github.com/londonaicentre/FLIP/issues/936); `deploy/compose.prod.yml` is a local
prod-image harness, not a deploy target). One target needs no abstraction, so there is no hub-only provider,
and the hub's Terraform is simply the `AWS/` root.

## AWS is the orchestrator

Whatever shape and infrastructure a trust uses, it joins a hub that lives in AWS, so the AWS provider owns the
outputs every trust needs in order to reach it — the FL participant kits in S3 — and its Makefile carries the
targets that hand them over:

- `provision-local-trust` runs `trust/deploy/ansible/onprem.yml` on the host and stages the FL kit from S3.
  This is the one known exception to the rule above: the play itself needs nothing from AWS, but its driver
  lives in this Makefile, which parses the hub env file at load and so cannot run on a host that holds only
  its kit file. Splitting the two halves is tracked under [#1213](https://github.com/londonaicentre/FLIP/issues/1213).

So neither node shape is hub-independent: the trust is still registered on the hub
(`make -C deploy/providers/AWS register-trusts KIT=<CODE> HUB=<hub>` — the AWS provider's
ECS-aware target; the root `make register-trust` is the dev-only, local-container form) and
still receives its kit file from the hub admin.

## Note on the AWS Terraform root

`AWS/` is a **single Terraform root with a single state file** (`backend.tf` → `key = "flip/terraform.tfstate"`)
holding the hub alone. Until FLIP#1438 it also created a trust EC2 in the hub's VPC; every trust now deploys
from its own root with its own state under `trust/deploy/terraform/`, linked to the hub only by its kit and its
registration.
