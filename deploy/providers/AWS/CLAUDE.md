# CLAUDE.md — AWS Deployment

## Terraform Files

| File | Resources |
|------|-----------|
| `main.tf` | Provider config, VPC, subnets, IGW, NAT, route tables, RDS instance, Secrets Manager, SES |
| `services.tf` | S3 buckets, Cognito |
| `rds_proxy.tf` | RDS Proxy + IAM DB auth (proxy, IAM role/policy, SG, `rds-db:connect`) — see FLIP#556 |
| `ecs.tf` | ECS cluster, ALB, target groups, listeners |
| `ecs_services.tf` | ECS Fargate services (flip-api, FL services) |
| `ecs_tasks.tf` | ECS task definitions for Central Hub services (flip-api + FL) |
| `ecs_efs_provision.tf` | EFS access points + ECS provisioning task for FL workspace |
| `ecs_sg.tf` | ECS security groups |
| `certificate.tf` | ACM certificates (ALB + CloudFront) |
| `cloudfront.tf` | CloudFront distribution for flip-ui |
| `iam_ecs.tf` | IAM roles, instance profiles, SSM policies |
| `parameter_store.tf` | SSM Parameter Store entries |
| `backend.tf` | S3 backend + DynamoDB lock |
| `variables.tf` | All Terraform variables with defaults |

## AWS Profiles

| Alias | Environment | Account |
| ------- | ------------- | --------- |
| `stag` | Staging | `flipstag` |
| `prod` | Production | `flipprod` |
| `FlipDeveloperAccess-080369786334` | Developer access | — |

## Key Deploy Commands

```bash
make full-deploy PROD=stag                   # Full staging deploy
make full-deploy PROD=true                    # Full prod deploy
make full-deploy-hybrid PROD=<stag|true> [LOCAL_TRUST_IP=<ip>]  # Hybrid with on-prem trust
make init/plan/apply                          # Terraform workflow
make deploy-centralhub                        # ECS force-redeploy + CloudFront UI
make deploy-trust                             # Deploy trust stack to EC2
make deploy-ui                                # Build + sync UI to S3 + invalidate CloudFront
make status                                   # Health checks
make ssh-config                               # Generate SSH config with SSM ProxyCommand
make forward-trust                            # SSM port forward all trust UIs
make provision-local-trust                     # Provision an on-prem trust host (run ON the host)
make allow-local-trust-nlb LOCAL_TRUST_IP=<ip>  # Open the FL-server NLB to a trust's reported IP
make package-onprem-trust-kit KIT=<CODE>      # Tarball a filled-in trust/.env.<CODE>.<env> + FL kit slice for an on-prem operator
make delete-trust NAME=<name>                 # Hard-delete a trust on the hub (frees the slot)
make destroy                                  # Selective destroy (preserves Cognito, Secrets, S3)
make aws-login                                # AWS SSO login
```

## Infrastructure

- **VPC**: 10.0.0.0/16, 2 AZs, public + private subnets
- **ECS Fargate**: Central Hub services (flip-api, fl-api-net-1, fl-server-net-1)
- **EC2**: Trust host (t3.xlarge, private subnet, SSM-only access)
- **RDS**: PostgreSQL in private subnets. In production flip-api connects through **RDS Proxy** using **IAM auth** (short-lived per-connection tokens); the proxy uses the RDS-managed master secret to reach the DB, so secret rotation no longer takes flip-api down (FLIP#556). No `rds_iam` Postgres grant is needed.
- **ALB**: Internal (`internal = true`, private subnets); HTTPS termination for `/api/*` reached via CloudFront VPC origin (no public IP)
- **NLB**: gRPC for FL server traffic
- **CloudFront + S3**: flip-ui static hosting
- **Secrets Manager**: `FLIP_API` secret (AES key, DB password, key hashes)
- **Cognito**: `flip-user-pool` with email auth
- **Container registry**: **GHCR** (`ghcr.io/londonaicentre/`) for every FLIP image (flip-api, flare-fl-api, flare-fl-server, flower-superlink, trust-api, imaging-api, data-access-api, orthanc, omop-db, XNAT). ECS Fargate task definitions pull directly from GHCR — `var.docker_registry` in `variables.tf` defaults to it; trust EC2 / on-prem hosts do too. **There is no ECR mirror.** A surgical centralhub redeploy (per `project_prod_ecs_deploy.md` — don't `make apply` for prod) is: GH workflow `workflow_dispatch` to build the branch image to GHCR → `aws ecs register-task-definition` with the branch tag → `aws ecs update-service --force-new-deployment`. The flip-ui bundle ships separately via `make deploy-ui` (it's static assets in S3, not a container image).

## Verifying a Central-Hub FL redeploy

A surgical FL redeploy is `aws ecs update-service --force-new-deployment` on `fl-server-net-1` / `fl-api-net-1` (Fargate re-pulls the `:stag`/`:prod` tag on every task start — no task-def revision needed). To confirm the new image is actually running, compare the task's image digest to the GHCR tag — **but select the container by name, not by array index**:

- `aws ecs describe-tasks` returns `containers` as an array, and the **GuardDuty Runtime Monitoring sidecar** (`aws-guardduty-agent-*`) usually sorts first. Querying `containers[0].imageDigest` reads the *agent's* digest, not the FL container's.
- The GuardDuty agent image lives in an AWS-internal ECR, **never GHCR**, so its digest returns **HTTP 404** when looked up in `ghcr.io`. A 404 digest is the sidecar — not a stale FL image. (This burned a full investigation on 2026-06-24: `66446df3…` was the GuardDuty agent; the real `fl-server-net-1` digest `29b10362…` matched `:stag` exactly. Both `flare-fl-server:stag` and `flare-fl-api:stag` were rebuilt by the #624 FL-deps merge, configs created `15:54–15:55Z`.)
- Always query `tasks[0].containers[?name=='fl-server-net-1'].imageDigest`. Full verification recipe (GHCR token, manifest digest, config `created` timestamp) is in [`TROUBLESHOOTING.md` §1.9](TROUBLESHOOTING.md).

## State Management

- Remote state in S3 (`FLIP_TFSTATE_BUCKET_NAME`) with DynamoDB locking
- Persistent resources (S3, Secrets, Cognito) preserved during destroy
- `make import-persistent` to import pre-existing resources
