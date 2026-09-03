# AGENTS.md — AWS Deployment

## Terraform Files

| File | Resources |
|------|-----------|
| `main.tf` | Provider config, VPC, subnets, IGW, NAT, route tables, RDS instance, Secrets Manager, SES |
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
| `ci/` | Separate root (`flip/ci/terraform.tfstate`): the GitHub Actions OIDC plan/apply roles. Applied from a laptop only — see `ci/README.md` |

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
make print-tf-env                             # Print resolved TF_VAR_* as KEY=value (consumed by the CI workflows)
make seed-ci-keypair-param                    # Publish the aws_key_pair public key from state to SSM, for CI plans
make -C ci init/plan/apply                    # GitHub Actions OIDC roles (laptop only — see ci/README.md)
make checkov-lint                             # Static checkov security lint (IAM policy content + promoted posture checks) — CI counterpart is the Checkov Security Lint job in validate_terraform.yml (FLIP#1052, FLIP#1058); suppress deliberate breadth/posture in-code with `# checkov:skip=<ID>:<rationale>`. NB this Makefile's parse-time env guard needs the deploy env file — the REPO-ROOT `make checkov-lint` (or `bash scripts/checkov_lint.sh`) runs env-free
```

## Terraform CI (FLIP#962)

Terraform runs in GitHub Actions as well as from a laptop. `terraform_plan.yml`
plans staging on every PR touching `deploy/providers/AWS/**`; `terraform_apply.yml`
applies on push to `develop` (stag) and `main` (prod); `terraform_drift.yml` plans
nightly and raises one issue per environment. All three authenticate via OIDC —
no long-lived AWS keys in GitHub. **Merging to `main` now changes production
infrastructure**; the old "don't `make apply` for prod" rule is superseded.

Things worth knowing before touching any of it:

- **The Makefile stays the single source of truth for Terraform inputs.** CI
  composes `.env.stag` / `.env.production` from GitHub environment secrets and
  variables (`scripts/compose-ci-env.sh`), then runs `make print-tf-env >> $GITHUB_ENV`.
  Adding an `export TF_VAR_…` line therefore also means adding the key to that
  script's manifest, to all three workflow `env:` blocks, and to both GitHub
  environments — `scripts/tests/test_compose_ci_env.sh` fails the build otherwise.
- **Env values now live in two places** (the operator `.env.<env>` file and the
  GitHub environment) with no automatic link. Missing keys fail loudly; drifted
  ones show up as an unexpected plan diff.
- **Use `aws-stag` / `aws-prod`, never the existing `flip` environment** — `flip`
  holds *test* values for `AES_KEY_BASE64` / `POSTGRES_PASSWORD` / `SES_VERIFIED_EMAIL`.
- **Every workflow resolves the image tag rather than reading it**
  (`scripts/resolve-image-tags.sh`): an apply takes this commit's `sha-<short7>`
  once published, else the tag the service is already running; plan and drift run
  it with `RESOLVE_SHA_TAG=false`, which reads only the running tag and never
  touches the registry. The configured `:stag`/`:prod` can never *replace* a
  deployed tag — that would discard the FLIP#751 pin — and a plan that read the
  configured tag would report a permanent `sha-… → :prod` diff nobody can clear.
  (Reusing `:stag` when that is genuinely what stag runs is a no-op, and expected.)
  Every lookup **fails closed**: absence is recognised only from ECS's own
  `failures[].reason == "MISSING"`, so an expired session or a wrong `ECS_CLUSTER`
  stops the run instead of reading as "empty account" and emitting the mutable tag.
- **An apply holds if the plan touches FL** (`scripts/check-fl-plan-impact.sh`):
  `fl-server-net-1` / `fl-api-net-1` task definitions or services, or any EFS
  deletion. `flip-api` is deliberately not watched. The hub cannot be asked
  whether a run is in flight — `/fl/quiesce` is Cognito-gated, CloudFront strips
  the internal key, and the DB is in a private subnet — so the plan is asked instead.
  Release a held apply by re-dispatching `terraform_apply.yml` with the
  `fl_quiesced: true` input; a plain re-run reads the same plan and holds again.
- **`aws-prod` admits `main` alone.** An environment's secrets are readable by any
  workflow that names it and runs on an admitted branch, before any AWS call, so
  admitting the default branch would hand the production secrets to every workflow
  merged to develop. The nightly drift run reaches prod by dispatching itself onto
  `main` rather than by widening the policy.
- **Every IAM role this root owns carries a permissions boundary**
  (`var.iam_permissions_boundary_name`, the policy declared in `ci/`). The CI apply
  role may only create a role, or write an inline policy onto one, when the role
  carries it — which is what keeps `PowerUserAccess` + IAM write from being
  administrator-equivalent. Adding a role means adding its literal name to
  `var.managed_role_names` in `ci/variables.tf` and re-applying `ci/` from a laptop
  first, or the apply cannot pass or re-trust it.
- **The pytest suite under `tests/` runs in CI** as the `Deploy Python tests` job in
  `validate_terraform.yml`. The root `make unit_test` does not reach this directory
  and `make -C deploy/providers/AWS test` cannot be used (parse-time env guard), so
  run it locally with `uv run --frozen pytest tests` from this directory.

- **Seed the GitHub environments with `scripts/setup-github-environments.sh`** (repo
  admin, `--dry-run` first). It derives the secret-vs-variable split from
  `terraform_plan.yml` rather than hard-coding it — a key stored as a variable but
  read as `secrets.X` resolves to empty and fails the run pointing at the wrong
  cause — and refuses when `ci/` is initialised for the other account.
- **Never seed a GitHub environment from a laptop `.env` file without checking it.**
  `scripts/reconcile_ci_env.py --env <e> --compare <file>` rebuilds the Terraform
  inputs from deployed state and reports drift (secrets shown as digests, never
  values). Staging's checked-out file was a batch of values stale, including a
  renamed UI bucket that plans as a `prevent_destroy` violation. Its `--out` writes
  only the Terraform inputs, so it refuses to overwrite a real operator env file,
  and on `--env prod` it treats an empty `DEMO_ASSETS_BUCKET_NAME` as a failed
  recovery rather than a value — empty there destroys the Ark+ demo resources.

Full flow, one-time setup and break-glass: [README.md](README.md#terraform-ci-plan-on-pr-apply-on-merge).

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
- **Container registry**: **GHCR** (`ghcr.io/londonaicentre/`) for every FLIP image (flip-api, flare-fl-api, flare-fl-server, flower-superlink, trust-api, imaging-api, data-access-api, orthanc, omop-db, XNAT). ECS Fargate task definitions pull directly from GHCR — `var.docker_registry` in `variables.tf` defaults to it; trust EC2 / on-prem hosts do too. **There is no ECR mirror.** A surgical centralhub redeploy is now one command: GH workflow `workflow_dispatch` to build the branch image to GHCR (publishes `sha-<short7>`) → `make deploy-centralhub TAG=sha-<short7>`, which registers new task-definition revisions and repoints the services (FLIP#751 — the previously manual register-task-definition + update-service runbook). The flip-ui bundle ships separately via `make deploy-ui` (it's static assets in S3, not a container image).

## Verifying a Central-Hub FL redeploy

An FL redeploy is `make deploy-centralhub` (branch-tip resolution, or `TAG=sha-<short7>` for a pinned build) — it registers new task-definition revisions for `fl-server-net-1` / `fl-api-net-1` pinning the immutable sha tag and repoints the services at them (FLIP#751; the old flow re-pulled the mutable `:stag`/`:prod` tag via `update-service --force-new-deployment`). Note it also rolls `flip-api` to the same tag and finishes with `make deploy-ui`, which builds `flip-ui` **from your local working tree** — run it from a clean checkout of the deployed branch. To confirm the new image is actually running, compare the task's image digest to the GHCR tag — **but select the container by name, not by array index**:

- `aws ecs describe-tasks` returns `containers` as an array, and the **GuardDuty Runtime Monitoring sidecar** (`aws-guardduty-agent-*`) usually sorts first. Querying `containers[0].imageDigest` reads the *agent's* digest, not the FL container's.
- The GuardDuty agent image lives in an AWS-internal ECR, **never GHCR**, so its digest returns **HTTP 404** when looked up in `ghcr.io`. A 404 digest is the sidecar — not a stale FL image. (This burned a full investigation on 2026-06-24: `66446df3…` was the GuardDuty agent; the real `fl-server-net-1` digest `29b10362…` matched `:stag` exactly. Both `flare-fl-server:stag` and `flare-fl-api:stag` were rebuilt by the #624 FL-deps merge, configs created `15:54–15:55Z`.)
- Always query `tasks[0].containers[?name=='fl-server-net-1'].imageDigest`. Full verification recipe (GHCR token, manifest digest, config `created` timestamp) is in [`TROUBLESHOOTING.md` §1.9](TROUBLESHOOTING.md).

## State Management

- Remote state in S3 (`FLIP_TFSTATE_BUCKET_NAME`) with S3 native locking (`use_lockfile = true` in `backend.tf`; no DynamoDB lock table)
- Persistent resources (S3, Secrets, Cognito) preserved during destroy
- `make import-persistent` to import pre-existing resources
