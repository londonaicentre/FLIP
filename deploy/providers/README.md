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

A **provider** provisions the *infrastructure* a FLIP component runs on — an AWS account, an on-prem host, a
Kubernetes cluster. It does not define the container stack itself; that lives in the compose files described
in [`../README.md#where-things-live`](../README.md#where-things-live).

| Provider | Deploys | Entry point |
| -------- | ------- | ----------- |
| [`AWS/`](AWS/README.md) | **Hub + optional cloud trust** — ECS Fargate, RDS + Proxy, ALB/NLB, CloudFront, Cognito, SES, and (when `DEPLOY_TRUST_EC2` is set) a trust EC2 host | `make -C deploy/providers/AWS full-deploy PROD=<stag\|true>` |
| [`kubernetes/`](kubernetes/README.md) | **Trust only** — Helm chart `flip-trust` | `make -C deploy/providers/kubernetes deploy-trust-k8s KIT=<CODE> PROD=<stag\|true>` |
| [`local/`](local/README.md) | **Trust only** — Ansible provisioning of an on-prem Ubuntu host | `make -C deploy/providers/AWS provision-local-trust KIT=<CODE> PROD=<stag\|true>` |

`KIT=<CODE>` names the trust whose kit file (`trust/.env.<CODE>.<env>`) the target reads; `PROD` selects that
file's environment suffix (`stag` when unset, `production` when `true`). Both are load-bearing on the
trust-side targets — `provision-local-trust` resolves `FL_KIT_SLOT` out of the kit file and aborts at the FL
kit download without it.

## Why two of three are trust-only

Deployment variability is almost entirely trust-side. A **trust** can run on AWS EC2, an on-prem Ubuntu box,
or a Kubernetes cluster — three targets, hence three provider recipes. The **Central Hub** has exactly one
supported production target, AWS ECS Fargate (hub-on-EC2 was deprecated in
[#936](https://github.com/londonaicentre/FLIP/issues/936); `deploy/compose.production.yml` is a local
prod-image harness, not a deploy target). One target needs no abstraction, so there is no hub-only provider.

## AWS is the orchestrator

The three providers are siblings, but the AWS provider drives the other two — it owns the Terraform outputs
(VPC, NLB security-group rules, FL participant kits in S3) that an on-prem or K8s trust needs in order to
reach the hub:

- `make -C deploy/providers/AWS provision-local-trust` runs `local/site_local_trust.yml`
- `make -C deploy/providers/AWS full-deploy-with-k8s` calls `$(MAKE) -C ../kubernetes sync-kit / up / status`

So a trust-only provider is not hub-independent: whichever one you use, the trust is still registered on the
hub (`make register-trust KIT=<CODE>`) and still receives its kit file from the hub admin.

## Note on the AWS Terraform root

`AWS/` is a **single Terraform root with a single state file** (`backend.tf` → `key = "flip/terraform.tfstate"`).
The cloud trust is instantiated from that same root (`AWS/main.tf`, `module "trust_ec2"`) and consumes
hub-owned resources — the hub's VPC subnets, security group, key pair and IAM instance profile. The cloud
trust runs *inside the hub's VPC*. It is therefore not separable into hub and trust halves without splitting
Terraform state; treat `AWS/` as one indivisible unit.
