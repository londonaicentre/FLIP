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
It does not define the container stack or configure a site-owned host. Both live under
[`trust/deploy/`](../../trust/deploy/README.md) (see [Where things live](../README.md#where-things-live)).

| Provider | Provisions | Entry point |
| -------- | ---------- | ----------- |
| [`AWS/`](AWS/README.md) | **Hub only** — ECS Fargate, RDS + Proxy, NLB, CloudFront, Cognito, SES and the SSM bastion. AWS trust nodes have their own roots under [`trust/deploy/terraform/aws/`](../../trust/deploy/terraform/aws/) | `make -C deploy/providers/AWS full-deploy HUB=<stag\|prod>` |

## How a trust node is deployed: shape × infrastructure

A trust node is one container stack in two shapes, and either shape can run on infrastructure a provider
created or on infrastructure the site already has. Keep the two axes apart when reading the tree:

| Node shape | Defined in | Installed by | Infrastructure it runs on |
| ---------- | ---------- | ------------ | ------------------------- |
| **Compose on a host** | [`trust/deploy/compose/compose_trust.*.yml`](../../trust/deploy/README.md) | [`trust/deploy/ansible/onprem.yml`](../../trust/deploy/ansible/README.md) (site-owned host), [`aws-ec2.yml`](../../trust/deploy/ansible/README.md) or [`azure.yml`](../../trust/deploy/ansible/README.md) (cloud VMs), then `make up-onprem-trust` / the EC2 Terraform root's `make deploy` | an on-prem Ubuntu box, or a VM from `trust/deploy/terraform/{aws/ec2,azure}` |
| **Helm on Kubernetes** | [`trust/deploy/helm/`](../../trust/deploy/helm/README.md) (chart `flip-trust`) | `make -C trust/deploy/helm deploy-trust-k8s KIT=<CODE> HUB=<stag\|prod>` | a site-managed cluster (k3s, on-prem) or a managed one (AKS, EKS) |

**Spell `HUB` out** on either row: `deploy/providers/AWS/Makefile` refuses to run without it, while
`trust/deploy/helm/Makefile` reads **the `development` kit when `HUB` is unset**.

## The Central Hub

The Central Hub has exactly one supported production target: AWS ECS Fargate. Hub-on-EC2 was deprecated in
[#936](https://github.com/londonaicentre/FLIP/issues/936). `deploy/compose.prod.yml` is a local prod-image
harness, not a deploy target. With a single target there is no provider abstraction for the hub: its Terraform
is simply the `AWS/` root.

## AWS is the orchestrator

Whatever shape and infrastructure a trust uses, it joins a hub that lives in AWS. The AWS provider therefore owns
what every trust needs to reach the hub: the trust's registration and its FL participant kit in S3. Its Makefile
carries the targets that hand these over.

`make -C deploy/providers/AWS provision-local-trust` runs `trust/deploy/ansible/onprem.yml` on the host and
stages the FL kit from S3.

This target is the one known exception to "providers hold only infrastructure". The play itself needs nothing from
AWS. Its driver, however, lives in this Makefile, which parses the hub env file at load. It therefore cannot run on
a host that holds only its kit file. [#1213](https://github.com/londonaicentre/FLIP/issues/1213) moved the play
under `trust/deploy/ansible/` but left this driver here.

Neither node shape is hub-independent. Every trust is registered on the hub and receives its kit file from the
hub admin. Register a trust with the AWS provider's ECS-aware target:

```bash
make -C deploy/providers/AWS register-trusts KIT=<CODE> HUB=<stag|prod>
```

The root `make register-trust` is the dev-only form, which registers against the local containers.

## Note on the AWS Terraform root

`AWS/` is a **single Terraform root with a single state file** (`backend.tf` → `key = "flip/terraform.tfstate"`)
holding the hub alone. Every trust deploys from its own root, with its own state, under `trust/deploy/terraform/`.
It is linked to the hub only by its kit and its registration. (Before FLIP#1438 this root also created a trust EC2
in the hub's VPC.)
