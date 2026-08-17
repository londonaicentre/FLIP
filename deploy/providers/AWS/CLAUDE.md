# CLAUDE.md — AWS Deployment

## Terraform Files

| File | Resources |
|------|-----------|
| `main.tf` | Provider config, VPC, subnets, IGW, NAT, route tables, RDS instance, Secrets Manager, SES |
| `network_lza.tf` | LZA platform-managed network (FLIP#749): VPC/subnet data lookups + the `local.vpc_id` / `local.app_subnet_ids` / `local.data_subnet_ids` locals both paths consume |
| `services.tf` | S3 buckets, Cognito |
| `rds_proxy.tf` | RDS Proxy + IAM DB auth (proxy, IAM role/policy, SG, `rds-db:connect`) — see FLIP#556 |
| `ecs.tf` | ECS cluster, capacity providers, ECS CloudWatch log groups (ALB / NLB / target groups / listener rules live in `main.tf`) |
| `ecs_services.tf` | ECS Fargate services (flip-api, FL services) |
| `ecs_tasks.tf` | ECS task definitions for Central Hub services (flip-api + FL) |
| `efs.tf` | EFS file system, mount targets, and access points (FL workspace) |
| `ecs_efs_provision.tf` | ECS provisioning task that pre-populates the FL EFS workspace (NVFLARE kit or Flower creds, by `fl_backend`) |
| `ecs_flower.tf` | Flower-only register-supernode-keys one-shot task + run-task trigger (FLIP#566) |
| `ecs_sg.tf` | ECS security groups |
| `certificate.tf` | ACM certificates (ALB + CloudFront) |
| `cloudfront.tf` | CloudFront distribution for flip-ui |
| `iam_ecs.tf` | IAM roles, instance profiles, SSM policies |
| `parameter_store.tf` | SSM Parameter Store entries |
| `backend.tf` | S3 backend with S3 native locking (`use_lockfile`) |
| `variables.tf` | All Terraform variables with defaults |

## AWS Profiles

| Alias | Environment | Account |
| ------- | ------------- | --------- |
| `stag` | Staging | `flipstag` |
| `prod` | Production | `flipprod` |
| `lza-prod` | LZA FLIPProduction (`PROD=lza`, FLIP#749; `FLIPAdminAccess` permission set) | `893493035022` |
| `dev` | Development (the `dev/` root: Cognito + SES; `FlipDeveloperAccess` permission set) | `flipdev` |

## Key Deploy Commands

```bash
make full-deploy PROD=stag                   # Full staging deploy
make full-deploy PROD=true                    # Full prod deploy
make init/plan/apply PROD=lza                 # LZA FLIPProduction (env-gated; full-deploy chains untested there — see README "Deploying to the LZA account")
make full-deploy-hybrid PROD=<stag|true> [LOCAL_TRUST_IP=<ip>]  # Hybrid with on-prem trust
make full-deploy-hub-only PROD=<stag|true>    # Hub only, NO cloud Trust EC2 (all trusts on-prem, e.g. GPU hosts) — see README "Hub-only Deployment"
make init/plan/apply                          # Terraform workflow
make deploy-centralhub                        # ECS deploy at env branch tip via sha-<short7> task-def revisions + CloudFront UI (FLIP#751; TAG= to pin). Prints an FL quiesce reminder (FLIP#770): enable Deployment Mode (pauses FL job pickup) + wait for the running job before deploying; GET /fl/quiesce reports deployment_mode + fl_quiesced. PROD=true additionally asks "Are you sure you want to continue?" (stag stays non-interactive)
make rollback-centralhub                      # Repoint ECS services at the previous ACTIVE task-def revision + deregister the rolled-away one
make deploy-trust                             # Deploy trust stack to EC2
make deploy-ui                                # Build + sync UI to S3 + invalidate CloudFront (excludes ark_demo/*)
make deploy-ark-demo                          # Build the public Ark+ demo SPA + sync to S3 under ark_demo/ + invalidate /ark_demo/*
make status                                   # Health checks
make ssh-config                               # Generate SSH config with SSM ProxyCommand
make forward-trust                            # SSM port forward all trust UIs
make provision-local-trust                     # Provision an on-prem trust host (run ON the host)
make allow-local-trust-nlb LOCAL_TRUST_IP=<ip>  # Open the FL-server NLB to a trust's reported IP
make package-onprem-trust-kit KIT=<CODE>      # Tarball a filled-in trust/.env.<CODE>.<env> + FL kit slice for an on-prem operator
make delete-trust NAME=<name>                 # Hard-delete a trust on the hub (frees the slot)
make add-fl-kits N=<n> PROD=<stag|true>       # Ensure N more claimable FL kit slots — activate spares first, mint only the shortfall (NVFLARE; no restart — see README)
make apply-fl-kit-slots                       # Targeted plan/apply of the /flip/fl_kit_slot_names SSM parameter (slot activation path)
make destroy                                  # Selective destroy (preserves Cognito, Secrets, S3)
make aws-login                                # AWS SSO login
```

## Infrastructure

- **VPC**: 10.0.0.0/16, 2 AZs, public + private subnets
- **ECS Fargate**: Central Hub services (flip-api, fl-api-net-1, fl-server-net-1). The FL task families serve **both FL backends** (FLIP#566): `FL_BACKEND` switches image/ports/command/env/mounts — NVFLARE (single port `FL_SERVER_PORT`, EFS kit from `fl-flare-participant-kits/<FLARE_KIT_DATE>`) vs Flower (SuperLink 9092 Fleet/9093 Exec/9097 health, TLS + SuperNode auth, creds from `fl-flower-participant-kits/<FLOWER_KIT_DATE>`, shared `fl_jobs` EFS volume, register-supernode-keys one-shot). The NLB listener stays on `FL_SERVER_PORT` for both; only the target-side port changes.
- **EC2**: Trust host (t3.xlarge, private subnet, SSM-only access)
- **RDS**: PostgreSQL in private subnets. In production flip-api connects through **RDS Proxy** using **IAM auth** (short-lived per-connection tokens); the proxy uses the RDS-managed master secret to reach the DB, so secret rotation no longer takes flip-api down (FLIP#556). No `rds_iam` Postgres grant is needed.
- **ALB**: Internal (`internal = true`, private subnets); HTTPS termination for `/api/*` reached via CloudFront VPC origin (no public IP)
- **NLB**: gRPC for FL server traffic
- **CloudFront + S3**: flip-ui static hosting
- **Secrets Manager**: `FLIP_API` secret (AES key, DB password, key hashes)
- **Cognito**: `flip-user-pool` with email auth
- **Container registry**: **GHCR** (`ghcr.io/londonaicentre/`) for every FLIP image (flip-api, flare-fl-api, flare-fl-server, flower-superlink, trust-api, imaging-api, data-access-api, orthanc, omop-db, XNAT). ECS Fargate task definitions pull directly from GHCR — `var.docker_registry` in `variables.tf` defaults to it; trust EC2 / on-prem hosts do too. **There is no ECR mirror.** A surgical centralhub redeploy (per `project_prod_ecs_deploy.md` — don't `make apply` for prod) is now one command: GH workflow `workflow_dispatch` to build the branch image to GHCR (publishes `sha-<short7>`) → `make deploy-centralhub TAG=sha-<short7>`, which registers new task-definition revisions and repoints the services (FLIP#751 — the previously manual register-task-definition + update-service runbook). The flip-ui bundle ships separately via `make deploy-ui` (it's static assets in S3, not a container image).

## Verifying a Central-Hub FL redeploy

An FL redeploy is `make deploy-centralhub` (branch-tip resolution, or `TAG=sha-<short7>` for a pinned build) — it registers new task-definition revisions for `fl-server-net-1` / `fl-api-net-1` pinning the immutable sha tag and repoints the services at them (FLIP#751; the old flow re-pulled the mutable `:stag`/`:prod` tag via `update-service --force-new-deployment`). Note it also rolls `flip-api` to the same tag and finishes with `make deploy-ui`, which builds `flip-ui` **from your local working tree** — run it from a clean checkout of the deployed branch. To confirm the new image is actually running, compare the task's image digest to the GHCR tag — **but select the container by name, not by array index**:

- `aws ecs describe-tasks` returns `containers` as an array, and the **GuardDuty Runtime Monitoring sidecar** (`aws-guardduty-agent-*`) usually sorts first. Querying `containers[0].imageDigest` reads the *agent's* digest, not the FL container's.
- The GuardDuty agent image lives in an AWS-internal ECR, **never GHCR**, so its digest returns **HTTP 404** when looked up in `ghcr.io`. A 404 digest is the sidecar — not a stale FL image. (This burned a full investigation on 2026-06-24: `66446df3…` was the GuardDuty agent; the real `fl-server-net-1` digest `29b10362…` matched `:stag` exactly. Both `flare-fl-server:stag` and `flare-fl-api:stag` were rebuilt by the #624 FL-deps merge, configs created `15:54–15:55Z`.)
- Always query `tasks[0].containers[?name=='fl-server-net-1'].imageDigest`. Full verification recipe (GHCR token, manifest digest, config `created` timestamp) is in [`TROUBLESHOOTING.md` §1.9](TROUBLESHOOTING.md).

## State Management

- Remote state in S3 (`FLIP_TFSTATE_BUCKET_NAME`) with S3 native locking (`use_lockfile = true` in `backend.tf`; no DynamoDB lock table)
- Persistent resources (S3, Secrets, Cognito) preserved during destroy
- `make import-persistent` to import pre-existing resources
