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

A **provider** provisions the *infrastructure* a FLIP component runs on — an AWS account today, an Azure
subscription next. It is Terraform (plus whatever host bootstrap that cloud needs) and nothing else: it does
not define the container stack, and it does not configure a host the operator already owns. Those two
concerns live next to the source tree whose images they run, under [`trust/deploy/`](../../trust/deploy/README.md)
(see [`../README.md#where-things-live`](../README.md#where-things-live) for the rule).

| Provider | Provisions | Entry point |
| -------- | ---------- | ----------- |
| [`AWS/`](AWS/README.md) | **Hub + optional cloud trust** — ECS Fargate, RDS + Proxy, ALB/NLB, CloudFront, Cognito, SES, and a trust EC2 host **unless `DEPLOY_TRUST_EC2=false`** (it defaults to `true`, and is settable from the env file as well as the CLI) | `make -C deploy/providers/AWS full-deploy KIT=<CODE> PROD=<stag\|true>`<br>hub-only: `full-deploy-hub-only PROD=<stag\|true>` |

`KIT=<CODE>` names the trust whose kit file (`trust/.env.<CODE>.<env>`) the target reads; `PROD` selects
that file's environment suffix. **Spell `PROD` out** — `deploy/providers/AWS/Makefile`'s `KIT_ENV_SUFFIX` is
two-valued (`production` when `PROD=true`, `stag` otherwise), while `trust/deploy/helm/Makefile`'s `ENV` is
three-valued (`production`, `stag`, and **`development` when `PROD` is unset**).

`KIT` is load-bearing, and omitting it costs a *partial* run rather than a clean failure: `full-deploy` pulls in
`deploy-trust` → `seed-trust-data`, whose `KIT is required` guard fires only after `plan`, `apply`,
`deploy-centralhub` and `register-trusts` have all run. Deploying the hub alone needs no `KIT`: use
`full-deploy-hub-only`.

## How a trust node is deployed: shape × infrastructure

A trust node is one container stack in two shapes, and either shape can run on infrastructure a provider
created or on infrastructure the site already has. Keep the two axes apart when reading the tree:

| Node shape | Defined in | Configured / installed by | Infrastructure it runs on |
| ---------- | ---------- | ------------------------- | ------------------------- |
| **Compose on a host** | [`trust/deploy/compose_trust.*.yml`](../../trust/deploy/README.md) | the Ansible plays — [`trust/deploy/ansible/onprem.yml`](../../trust/deploy/ansible/README.md) for a site-owned host, [`AWS/site.yml`](AWS/site.yml) for the EC2 trust — then `make -C trust up-trust` / `up-onprem-trust` / `up-trust-ec2` | an on-prem Ubuntu box, the EC2 trust this provider creates, or (planned, FLIP#1213) an Azure VM |
| **Helm on Kubernetes** | [`trust/deploy/helm/`](../../trust/deploy/helm/README.md) (chart `flip-trust`) | `make -C trust/deploy/helm deploy-trust-k8s KIT=<CODE> PROD=<stag\|true>` | a site-managed cluster (k3s, on-prem), or a managed one (AKS, EKS) |

The **Central Hub** has exactly one supported production target, AWS ECS Fargate (hub-on-EC2 was deprecated in
[#936](https://github.com/londonaicentre/FLIP/issues/936); `deploy/compose.production.yml` is a local
prod-image harness, not a deploy target). One target needs no abstraction, so there is no hub-only provider,
and the hub's Terraform is simply the `AWS/` root.

## AWS is still the orchestrator

Whatever shape and infrastructure a trust uses, it joins a hub that lives in AWS, so the AWS provider owns the
outputs every trust needs in order to reach it — the NLB security-group rules and the FL participant kits in
S3 — and its Makefile carries the targets that hand them over:

- `make -C deploy/providers/AWS provision-local-trust` runs `trust/deploy/ansible/onprem.yml` and stages the
  FL kit from S3
- `make -C deploy/providers/AWS full-deploy-with-k8s` calls `$(MAKE) -C ../../../trust/deploy/helm sync-kit / up / status`
- `allow-local-trust-nlb` / `add-k8s-trust` open the FL-server NLB to a trust's public IP

So neither node shape is hub-independent: the trust is still registered on the hub
(`make register-trust KIT=<CODE>`) and still receives its kit file from the hub admin.

## Note on the AWS Terraform root

`AWS/` is a **single Terraform root with a single state file** (`backend.tf` → `key = "flip/terraform.tfstate"`).
The cloud trust is instantiated from that same root (`AWS/main.tf`, `module "trust_ec2"`) and consumes
hub-owned resources — the hub's VPC subnets, security group, key pair and IAM instance profile. The cloud
trust runs *inside the hub's VPC*. It is therefore not separable into hub and trust halves without splitting
Terraform state; treat `AWS/` as one indivisible unit. A future cloud provider (Azure) should be its own root
with its own state, not a module of this one.
