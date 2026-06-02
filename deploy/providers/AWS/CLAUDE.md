# CLAUDE.md — AWS Deployment

## Terraform Files

| File | Resources |
| ------ | ----------- |
| `main.tf` | Provider config, VPC, subnets, IGW, NAT, route tables |
| `services.tf` | RDS, Secrets Manager, Cognito, SES |
| `ecs.tf` | ECS cluster, task definitions, IAM execution roles |
| `ecs_services.tf` | ECS Fargate services (flip-api, FL services) |
| `ecs_tasks.tf` | ECS task definitions for Central Hub services |
| `ecs_fl.tf` | FL-specific ECS resources |
| `certificate.tf` | ACM certificates (ALB + CloudFront) |
| `cloudfront.tf` | CloudFront distribution for flip-ui |
| `iam.tf` | IAM roles, instance profiles, SSM policies |
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
make full-deploy-stag-hybrid LOCAL_TRUST_IP=<ip>  # Hybrid with on-prem trust
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
- **RDS**: PostgreSQL in private subnets
- **ALB**: Internal (`internal = true`, private subnets); HTTPS termination for `/api/*` reached via CloudFront VPC origin (no public IP)
- **NLB**: gRPC for FL server traffic
- **CloudFront + S3**: flip-ui static hosting
- **Secrets Manager**: `FLIP_API` secret (AES key, DB password, key hashes)
- **Cognito**: `flip-user-pool` with email auth
- **Container registry**: **GHCR** (`ghcr.io/londonaicentre/`) for every FLIP image (flip-api, flare-fl-api, flare-fl-server, flower-superlink, trust-api, imaging-api, data-access-api, orthanc, omop-db, XNAT). ECS Fargate task definitions pull directly from GHCR — `var.docker_registry` in `variables.tf` defaults to it; trust EC2 / on-prem hosts do too. **There is no ECR mirror.** A surgical centralhub redeploy (per `project_prod_ecs_deploy.md` — don't `make apply` for prod) is: GH workflow `workflow_dispatch` to build the branch image to GHCR → `aws ecs register-task-definition` with the branch tag → `aws ecs update-service --force-new-deployment`. The flip-ui bundle ships separately via `make deploy-ui` (it's static assets in S3, not a container image).

## State Management

- Remote state in S3 (`FLIP_TFSTATE_BUCKET_NAME`) with DynamoDB locking
- Persistent resources (S3, Secrets, Cognito) preserved during destroy
- `make import-persistent` to import pre-existing resources
