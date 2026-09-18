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
| [`AWS/`](AWS/README.md) | **Hub + optional cloud trust** — ECS Fargate, RDS + Proxy, ALB/NLB, CloudFront, Cognito, SES, and a trust EC2 host **unless `DEPLOY_TRUST_EC2=false`** (it defaults to `true`, and is settable from the env file as well as the CLI) | `make -C deploy/providers/AWS full-deploy KIT=<CODE> PROD=<stag\|true>`<br>hub-only: `full-deploy-hub-only PROD=<stag\|true>` |

`KIT=<CODE>` names the trust whose kit file (`trust/.env.<CODE>.<env>`) the target reads; `PROD` selects
that file's environment suffix. `KIT` is load-bearing, and omitting it costs a *partial* run rather than a
clean failure: `full-deploy` pulls in `deploy-trust` → `seed-trust-data`, whose `KIT is required` guard fires
only after `plan`, `apply`, `deploy-centralhub` and `register-trusts` have all run. Deploying the hub alone
needs no `KIT`: use `full-deploy-hub-only`.

## How a trust node is deployed: shape × infrastructure

A trust node is one container stack in two shapes, and either shape can run on infrastructure a provider
created or on infrastructure the site already has. Keep the two axes apart when reading the tree:

| Node shape | Defined in | Installed by | Infrastructure it runs on |
| ---------- | ---------- | ------------ | ------------------------- |
| **Compose on a host** | [`trust/deploy/compose_trust.*.yml`](../../trust/deploy/README.md) | [`trust/deploy/ansible/onprem.yml`](../../trust/deploy/ansible/README.md) (site-owned host) or [`AWS/site.yml`](AWS/site.yml) (EC2), then `make up-onprem-trust` / `make -C trust up-trust-ec2` | an on-prem Ubuntu box, or the EC2 trust this provider creates |
| **Helm on Kubernetes** | [`trust/deploy/helm/`](../../trust/deploy/helm/README.md) (chart `flip-trust`) | `make -C trust/deploy/helm deploy-trust-k8s KIT=<CODE> PROD=<stag\|true>` | a site-managed cluster (k3s, on-prem) or a managed one (AKS, EKS) |

**Spell `PROD` out** on either row: `deploy/providers/AWS/Makefile`'s `KIT_ENV_SUFFIX` is two-valued
(`production` when `PROD=true`, `stag` otherwise), while `trust/deploy/helm/Makefile`'s `ENV` is three-valued
(`production`, `stag`, and **`development` when `PROD` is unset**).

The **Central Hub** has exactly one supported production target, AWS ECS Fargate (hub-on-EC2 was deprecated in
[#936](https://github.com/londonaicentre/FLIP/issues/936); `deploy/compose.production.yml` is a local
prod-image harness, not a deploy target). One target needs no abstraction, so there is no hub-only provider,
and the hub's Terraform is simply the `AWS/` root.

## AWS is the orchestrator

Whatever shape and infrastructure a trust uses, it joins a hub that lives in AWS, so the AWS provider owns the
outputs every trust needs in order to reach it — the NLB security-group rules and the FL participant kits in
S3 — and its Makefile carries the targets that hand them over:

- `provision-local-trust` runs `trust/deploy/ansible/onprem.yml` on the host and stages the FL kit from S3.
  This is the one known exception to the rule above: the play itself needs nothing from AWS, but its driver
  lives in this Makefile, which parses the hub env file at load and so cannot run on a host that holds only
  its kit file. Splitting the two halves is tracked under [#1213](https://github.com/londonaicentre/FLIP/issues/1213).
- `full-deploy-with-k8s` calls the chart's `sync-kit` / `up` / `status` targets (`make -C trust/deploy/helm …`)
- `allow-local-trust-nlb` / `add-k8s-trust` open the FL-server NLB to a trust's public IP

So neither node shape is hub-independent: the trust is still registered on the hub
(`make -C deploy/providers/AWS register-trusts KIT=<CODE>` — the AWS provider's
ECS-aware target; the root `make register-trust` is the dev-only, local-container form) and
still receives its kit file from the hub admin.

## Note on the AWS Terraform root

`AWS/` is a **single Terraform root with a single state file** (`backend.tf` → `key = "flip/terraform.tfstate"`).
The cloud trust is instantiated from that same root (`AWS/main.tf`, `module "trust_ec2"`) and consumes
hub-owned resources — the hub's VPC subnets, security group, key pair and IAM instance profile. The cloud
trust runs *inside the hub's VPC*. It is therefore not separable into hub and trust halves without splitting
Terraform state; treat `AWS/` as one indivisible unit. A second cloud provider should be its own root with its
own state, not a module of this one.
