# FLIP AWS Deployment Troubleshooting Guide

Operator troubleshooting for the Central Hub on AWS and the AWS-hosted trust now lives in the docs:
**[Troubleshooting](https://londonaicentreflip.readthedocs.io/en/latest/troubleshooting.html)**, with pages for the
[Central Hub on AWS](https://londonaicentreflip.readthedocs.io/en/latest/troubleshooting/central-hub-aws.html),
[trust nodes on Docker Compose](https://londonaicentreflip.readthedocs.io/en/latest/troubleshooting/trust-node-compose.html)
and [FL training and projects](https://londonaicentreflip.readthedocs.io/en/latest/troubleshooting/fl-training-and-projects.html). Between them they cover SSO expiry, UI sign-in after a
Cognito change, `NET_ENDPOINTS`, kit signatures, Orthanc mounts and seeding, trust disk space, the XNAT setup page and
service-account password, imaging-api hangs and the `num_samples=0` permission fault.

What stays here is maintainer material: Terraform state, regressions guarded in code, CI and image builds, and the
hub-database queries. Section numbers are kept as they were, because code and other docs cite them (§1.8, §4.3, §5);
the gaps are entries that moved to the docs.

---

## 1. Infrastructure (Terraform / AWS)

### 1.1 Terraform plan shows massive destroy + create (not additive)

**Symptom**: `terraform plan` shows 30–50 destroys and 50–80 creates instead of a small additive change.

**Root cause**: The S3 Terraform state (`flip/terraform.tfstate`) contains resources from a previous branch or failed deployment that don't exist in the current code. Terraform tries to destroy the old resources and create the new ones with different names.

**Fix**:

```bash
# List stale resources
terraform state list | grep -iE "ecs|efs|service_disc"

# Remove them from state (does not delete from AWS)
terraform state rm aws_ecs_cluster.flip
terraform state rm aws_efs_file_system.fl_data
# ... repeat for all stale resources

# Alternatively, destroy everything and start fresh:
make destroy HUB=stag
make full-deploy HUB=stag
```

---

### 1.2 Resource already exists in AWS but not in state

**Symptom**: `terraform apply` fails with "already exists" for resources like CloudWatch log groups, SSH key pairs, Route53 records, or VPC endpoints.

**Root cause**: Resources were created by a previous deployment (or a different Terraform state) and are orphaned in AWS.

**Fix**: Import the resource into Terraform state:

```bash
# CloudWatch log groups
terraform import aws_cloudwatch_log_group.ecs_flip_api /ecs/flip-api

# SSH key pair
terraform import aws_key_pair.flip_keypair flip-keypair

# Route53 record (format: ZONEID_RECORDNAME_TYPE)
terraform import aws_route53_record.fl_server_nlb Z0477233CC4IIHRHLWJS_fl.stag.flip.aicentre.co.uk_A

# S3 VPC endpoint
aws ec2 describe-vpc-endpoints --filters "Name=vpc-id,Values=<vpc-id>" "Name=service-name,Values=com.amazonaws.eu-west-2.s3"
terraform import aws_vpc_endpoint.s3 <vpc-endpoint-id>
```

If the existing resource has a mismatched attribute (e.g., different public key for the key pair), delete it in AWS first, then re-apply.

---

### 1.3 Ansible `community.general.terraform` fails on S3 backend

**Symptom**: `make ansible-init` fails with `Missing required argument on backend.tf` even after `terraform init`.

**Root cause**: The `community.general.terraform` Ansible module always runs `terraform validate` before `terraform init`, and the S3 backend in `backend.tf` has no `bucket` or `region` (they are passed at init time via `-backend-config`).

**Fix**: The `site.yml` playbook has been updated to use raw `terraform init` + `terraform output -json` instead of the module. If you encounter this on other branches:

```yaml
- name: initialize Terraform backend
  command: >
    terraform init
    -backend-config="bucket={{ lookup('env', 'FLIP_TFSTATE_BUCKET_NAME') }}"
    -backend-config="region={{ lookup('env', 'AWS_REGION') }}"
    -reconfigure
  args:
    chdir: ./
  changed_when: false

- name: extract Terraform outputs
  command: terraform output -json
  args:
    chdir: ./
  register: tf_out
  changed_when: false
```

---

### 1.6 ALB returns 502 / NLB shows unhealthy targets after ECS cutover

**Symptom**: After bringing ECS Fargate up alongside the legacy EC2 stack, the ALB still returns 502 for `/api/*` and the NLB target group shows the legacy EC2 target as unhealthy. Trust-side fl-client cannot reach fl-server. trust-api heartbeats may also return 401 because they hit the legacy flip-api whose secret hashes are stale.

**Root cause**: The ALB listener rule and NLB TCP listener still forward to the EC2-instance target groups. Adding ECS services without `load_balancer` blocks does not register the task ENI IPs anywhere — the LBs keep routing to the now-stopped EC2 containers.

**Fix** (already in place on `develop`): in `main.tf`, ECS task IPs are registered to dedicated `target_type = "ip"` target groups (`ecs_flip_api`, `ecs_fl_server_tcp`) and the listener rules / default actions point at them. The `ecs_services.tf` services include `load_balancer` blocks so each running task auto-registers. The legacy module-managed `ec2-instance-fl-server-tcp` target group is removed from the NLB module config so it stops registering an unhealthy EC2 target.

If you regress this: confirm the ALB listener rule's `target_group_arn` is `aws_lb_target_group.ecs_flip_api.arn` and the NLB TCP listener default action targets `aws_lb_target_group.ecs_fl_server_tcp.arn`. `target_type = "ip"` is mandatory for awsvpc Fargate — `instance` will not register.

---

### 1.7 ECS-internal call from fl-api → fl-server hangs in `try_connect`

**Symptom**: On ECS `fl-api-net-1` boots, registers in Cloud Map, but its FLARE admin client hangs trying to reach fl-server. `fl-server-net-1` is healthy on the NLB and reachable from the trust EC2.

**Root cause**: fl-api is the FLARE admin client and connects directly to fl-server inside the VPC (the NLB path is reserved for off-VPC trust FL clients). The fl-server SG had no ingress on 8002 from the fl-api SG, so the in-VPC connect attempt timed out.

**Fix**: `ecs_sg.tf` now opens fl-server SG ingress on 8002 from the fl-api SG. If you add another in-VPC FLARE admin consumer, add its SG to the same ingress rule.

---

### 1.8 Verifying which FL image an ECS task pulled — GuardDuty sidecar digest trap

**Symptom**: After a `make deploy-centralhub` (or a legacy `--force-new-deployment`) of `fl-server-net-1` / `fl-api-net-1`, you check the running task's image digest against the GHCR tag — the pinned `sha-<short7>` tag since FLIP#751, the mutable `:stag`/`:prod` tag before it — to confirm the new build is live. One container's `imageDigest` does **not** match the GHCR manifest, and worse, querying GHCR for that digest returns **HTTP 404** (it does not exist in GHCR at all). Looks like the task is running a stale/unknown image.

**Root cause**: GuardDuty Runtime Monitoring injects a sidecar container (`aws-guardduty-agent-*`) into every Fargate task. `aws ecs describe-tasks` returns `containers` as an **array**, and the GuardDuty agent often sorts **first** — so a query like `containers[0].imageDigest` reads the *agent's* digest, not the FL app container's. The GuardDuty agent image lives in an **AWS-internal ECR**, never GHCR, which is exactly why its digest 404s when you look it up in `ghcr.io`. The FL app container is a *different* element of the same array and its digest matches GHCR fine.

**Fix**: Select the container **by name**, never by index:

```bash
TASK_ARN=$(aws ecs list-tasks --cluster flip-cluster --service-name fl-server-net-1 \
  --profile prod --region eu-west-2 --query 'taskArns[0]' --output text)
aws ecs describe-tasks --cluster flip-cluster --tasks "$TASK_ARN" \
  --profile prod --region eu-west-2 \
  --query "tasks[0].containers[?name=='fl-server-net-1'].imageDigest" --output text
# Compare against the GHCR tag (anonymous pull token):
IMG=flare-fl-server
TOKEN=$(curl -s "https://ghcr.io/token?scope=repository:londonaicentre/$IMG:pull" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -s -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/vnd.oci.image.manifest.v1+json" \
  -H "Accept: application/vnd.docker.distribution.manifest.v2+json" \
  -D - -o /dev/null "https://ghcr.io/v2/londonaicentre/$IMG/manifests/stag" \
  | grep -i docker-content-digest
```

The two digests will match. To confirm the tag was rebuilt by a specific merge, fetch the config blob's `created` timestamp (follow the redirect with `curl -sL` on `/v2/.../blobs/<config-digest>`) — e.g. the #624 FL-deps rebuild produced `flare-fl-server:stag` / `flare-fl-api:stag` configs created `2026-06-24T15:54–15:55Z`, immediately after the develop merge's FL image build completed (~15:55Z). A digest that 404s in GHCR is the GuardDuty sidecar, not a stale FL image.

---

## 2. Deployment (Ansible / Docker)

### 2.2 Container images missing for current branch tag

**Symptom**: `make deploy-trust` fails with `docker manifest inspect` returning "no such manifest", or a Central Hub ECS deployment reports `CannotPullContainerError`.

**Root cause**: The `DOCKER_TAG` in `.env.stag` refers to a branch whose images haven't been built. GitHub Actions only auto-publish to GHCR on merges to `develop` and `main`. Branch images require manual `workflow_dispatch`.

**Fix**:

```bash
# Option A: Use a tag that has images
sed -i 's/^DOCKER_TAG=.*/DOCKER_TAG=develop/' .env.stag

# Option B: Trigger builds for specific branch
gh workflow run docker_build_trust_trust_api.yml --ref <branch>
gh workflow run docker_build_trust_imaging_api.yml --ref <branch>
gh workflow run docker_build_trust_data_access_api.yml --ref <branch>
# Wait for green, then use branch tag
```

---

### 2.7 `make deploy-ui` fails with `InvalidAccessKeyId` despite a successful SSO login

**Symptom**: `make -C deploy/providers/AWS deploy-ui HUB=stag` builds the bundle successfully, then fails on the first `aws s3` call:

```
📦 Syncing static assets to s3://flipstag/ ...
fatal error: An error occurred (InvalidAccessKeyId) when calling the ListObjectsV2 operation:
The AWS Access Key Id you provided does not exist in our records.
make: *** [Makefile:388: deploy-ui] Error 1
```

`aws sso login` reported success seconds earlier, and other Make targets (`make plan`, `make apply`, `make status`) work in the same shell.

**Root cause**: Same class of bug as Terraform SSO poisoning. The parent shell exports `AWS_ACCESS_KEY_ID=` (empty) or a stale value from a prior `.env*` source, and the AWS SDK's credential chain prefers env vars over the SSO profile. The other recipes were patched with an `unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN;` prefix (the `_AWS_ENV` macro at the top of the Makefile, or `_TF` for `terraform` calls), but each Make recipe line runs in its own shell — so every `aws` / `terraform` invocation needs the unset on its own line. `deploy-ui` was missed.

**Fix**: Prefix every `aws` call in the `deploy-ui` recipe with `$(_AWS_ENV)` (which expands to `unset AWS_* … ; AWS_PROFILE=$(AWS_PROFILE)`). Already applied — if you see this on a fresh checkout, confirm the recipe matches:

```bash
sed -n '/^deploy-ui:/,/^$/p' deploy/providers/AWS/Makefile | grep -c '_AWS_ENV'
# Expect 6 (2× terraform output + s3 sync + 2× s3 cp + cloudfront create-invalidation)
```

**Prevention**: When adding a new Make recipe that calls `aws`, `terraform`, `ansible`, or any script that uses boto3 / the AWS SDK, always prefix with `$(_AWS_ENV)` (or `$(_TF)` for `terraform`, or a bare `unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN;` on lines that run neither macro). Never trust the parent shell's AWS env vars.

## 3. Application (Pipeline / API)

### 3.1 API returns 401/403 on all authenticated routes (MFA enforcement)

**Symptom**: All authenticated API calls return `401 Unauthorized` or `403 Forbidden`. The UI shows "Something went wrong" but the login works.

**Root cause**: `ENFORCE_MFA=true` (the Settings default in `flip-api/src/flip_api/config.py:85`) gates every authenticated route on TOTP enrollment. The prod compose file (`deploy/compose.prod.yml`) did not pass `ENFORCE_MFA`, so the default `true` always applied.

**Fix**: The compose file now passes `ENFORCE_MFA=${ENFORCE_MFA:-true}` (inheriting the secure default). To temporarily disable for testing, set in `.env.stag`:

```
ENFORCE_MFA=false
```

Then redeploy: `make deploy-centralhub HUB=stag`

---

### 3.3 Imaging pipeline stuck on "Awaiting creation..." — hub-database workaround

The operator entry (causes, what to check at the trust) is
[A Trust stays on "Awaiting creation…"](https://londonaicentreflip.readthedocs.io/en/latest/troubleshooting/fl-training-and-projects.html#ts-awaiting-creation).
A new project's `last_reimport` defaults to its creation time, so the reimport scheduler
(`SCHEDULER_REIMPORT_IMAGING_PROJECT_STUDIES_RATE`, 30 min) skips it until `PROJECT_REIMPORT_RATE` (60 min) has passed.

**Workaround** (force the import for a stuck project):

```sql
-- Connect with psql using the bastion recipe in §5, then check the task.
SELECT id, task_type, status, created_at
FROM trust_task
WHERE task_type = 'CREATE_IMAGING'
ORDER BY created_at DESC
LIMIT 10;

UPDATE trust_task SET status='PENDING' WHERE id='<task_id>';

UPDATE xnat_project_status SET last_reimport='2020-01-01' WHERE xnat_project_id='<id>';
```

---

### 3.7 Single net permanently starved of new training jobs

**Symptom**: One FL net never picks up new training jobs even though scheduler logs show jobs being queued. Other nets are fine.

**Root cause**: `FLScheduler` rows transition to `BUSY` while a job runs. If `prepare_and_start_training` raised mid-flight in a previous cycle, the row was left `BUSY` with no live job — `run_jobs_core` then refused to schedule new work for that net forever.

**Fix** (already in place on `develop`): `_recover_stale_busy_schedulers()` runs at the top of every `run_jobs_core` cycle and resets `BUSY` rows whose associated job no longer exists. No manual intervention needed; just confirm the next cycle clears the row.

---

## 4. Configuration

### 4.3 `ENFORCE_MFA` env var not passed to container

**Symptom**: All authenticated routes return 401/403 despite `ENFORCE_MFA=false` in `.env.stag`.

**Root cause**: The prod compose file (`deploy/compose.prod.yml`) did not include `ENFORCE_MFA` in the flip-api environment block. The Pydantic Settings default (`true`) was never overridden.

**Fix**: The compose file now passes `ENFORCE_MFA=${ENFORCE_MFA:-true}` so the env var can be overridden. To disable MFA in staging, add `ENFORCE_MFA=false` to `.env.stag` and redeploy. Note: the default in compose is `true` (secure by default) — you must explicitly set `false` to disable.

---

### 4.4 DHCP options change not applied to running instances

**Symptom**: After deploying the ECS foundation, `flip.local` domain resolution doesn't work from existing EC2 instances.

**Root cause**: The `dhcp.tf` resource associates a new DHCP options set (with `flip.local` search domain) to the VPC. Existing EC2 instances won't pick up the new options until their DHCP lease expires and renews (typically 24-72 hours for AWS default, or a reboot). Until renewal, the instances continue using the previous (default) DHCP options.

**Fix**: Reboot the EC2 instance to force an immediate DHCP renewal:

```bash
aws ec2 reboot-instances --instance-ids <instance-id> --profile lza-stag   # or lza-prod
```

Or wait for the lease to renew naturally. This only matters if the instance needs to resolve `flip.local` domains (which it doesn't during PR 1 — ECS Fargate tasks are the consumers in PR 2).

---

## 5. Verification Commands

### Quick health check

```bash
make status HUB=stag
```

### Connect to RDS through the Central Hub bastion

The bastion has `psql` installed and its security group is allowed to reach RDS on port 5432. It deliberately has no Secrets Manager permission, so obtain the database password through the approved operator channel and enter it only at the interactive prompt.

```bash
cd deploy/providers/AWS
make ssh-config HUB=stag
terraform output -raw DbEndpoint
ssh flip

# On the bastion; substitute the endpoint printed above and the non-secret
# POSTGRES_USER / POSTGRES_DB values from .env.stag.
psql --host=<db-endpoint> --username=<POSTGRES_USER> --dbname=<POSTGRES_DB> --password
```

No inbound SSH or PostgreSQL rule is needed on the bastion: SSH travels through SSM, and the existing RDS rule allows port 5432 from the bastion security group.

### Check specific pipeline task status

```sql
SELECT task_type, status, created_at
FROM trust_task
WHERE trust_id = '<trust-id>'
ORDER BY created_at DESC
LIMIT 10;
```

### Check for stuck XNAT projects (last_reimport within last hour, zero reimports)

```sql
SELECT xnat_project_id, last_reimport, reimport_count FROM xnat_project_status WHERE last_reimport > NOW() - INTERVAL '1 hour' AND reimport_count = 0;
```

### Scan container logs for errors

```bash
# Central Hub ECS service
AWS_PROFILE=stag aws logs tail /ecs/flip-api --since 10m | grep -iE 'ERROR|Exception|Traceback' | tail -20

# Trust
ssh flip-trust "docker logs trust1-trust-api-1 2>&1 | grep -iE 'ERROR|ReadTimeout|502' | tail -20"
```

### Test XNAT connectivity from imaging-api

```bash
ssh flip-trust "docker exec trust1-imaging-api-1 python3 -c \"
import os, requests
r = requests.get('http://xnat-web:8080/data/projects',
    auth=(os.environ.get('XNAT_SERVICE_USER', 'flipServiceAccount'),
          os.environ.get('XNAT_SERVICE_PASSWORD', '')), timeout=10)
print(f'HTTP {r.status_code}')
\""
```

### Verify FL server clients

```bash
AWS_PROFILE=stag aws logs tail /ecs/fl-server-net-1 --since 10m | grep -E 'Client|Re-activate' | tail -5
```
