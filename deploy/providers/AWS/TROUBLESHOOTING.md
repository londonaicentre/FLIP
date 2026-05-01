# FLIP AWS Deployment Troubleshooting Guide

Common failures encountered during staging/production deployment and how to diagnose + resolve them. Each entry includes symptoms, root cause, and fix.

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
make destroy PROD=stag
make full-deploy PROD=stag
```

---

### 1.2 IAM permission denied for EFS or Service Discovery

**Symptom**:

```
Error: AccessDeniedException: elasticfilesystem:TagResource
Error: AccessDeniedException: servicediscovery:CreatePrivateDnsNamespace
```

**Root cause**: The `FlipDeveloperAccess` SSO permission set lacks `elasticfilesystem:*` and `servicediscovery:*` actions.

**Fix**: Add these permissions to the IAM inline policy in the `aicentre-iac` repository at `iam_flip_developer_inline_policy.tf`:

```hcl
statement {
  sid    = "EFSFullAccess"
  effect = "Allow"
  actions = ["elasticfilesystem:*"]
  resources = ["*"]
}
statement {
  sid    = "ServiceDiscoveryFullAccess"
  effect = "Allow"
  actions = ["servicediscovery:*"]
  resources = ["*"]
}
```

Merge the PR and re-sign in to AWS SSO.

---

### 1.3 Resource already exists in AWS but not in state

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

### 1.4 Ansible `community.general.terraform` fails on S3 backend

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

### 1.5 AWS SSO token expired

**Symptom**: All `aws` commands return `Unable to locate credentials` or `AccessDenied`.

**Fix**:

```bash
aws sso login --profile FlipDeveloperAccess-080369786334 --use-device-code
```

Use `--use-device-code` for headless/SSH environments.

---

## 2. Deployment (Ansible / Docker)

### 2.1 Docker volume mount parse failure (`empty section between colons`)

**Symptom**: `make deploy-trust` fails with:

```
invalid spec: :/var/lib/orthanc/db: empty section between colons
```

**Root cause**: The `ORTHANC_STORAGE_DIR_TRUST_1` environment variable is missing from the `.env.stag` file, resulting in an empty host path for the Orthanc Docker volume mount.

**Fix**: Add to `.env.stag`:

```
ORTHANC_STORAGE_DIR_TRUST_1=/opt/flip/orthanc/orthanc-storage
```

---

### 2.2 Docker images missing for current branch tag

**Symptom**: `make deploy-trust` / `deploy-centralhub` fails with `docker manifest inspect` returning "no such manifest".

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

### 2.3 XNAT returns setup page instead of API

**Symptom**: `CREATE_IMAGING` tasks fail with HTTP 500 containing HTML (`<title>XNAT Setup</title>`).

**Root cause**: After `make full-deploy`, XNAT was just configured (Ansible wrote setup configs) and the XNAT Tomcat is serving its setup page. The container needs a full restart cycle to pick up the saved configuration.

**Fix**:

```bash
ssh flip-trust "docker service update --force xnat1_xnat-web"
# Wait 90 seconds for Tomcat to start (it takes ~85s)
```

---

### 2.4 XNAT authentication fails for imaging-api

**Symptom**: `CREATE_IMAGING` / `REIMPORT_STUDIES` fail with `ReadTimeout` from trust-api → imaging-api → XNAT.

**Root cause**: Multiple causes:

1. **Credentials**: The imaging-api authenticates to XNAT using `XNAT_SERVICE_USER` and `XNAT_SERVICE_PASSWORD` (from `.env.stag` or Secrets Manager). Verify the deployed values match the current XNAT service account credentials — do not hardcode real values in config files or documentation.
2. **Timeout**: The imaging-api's `requests` calls to XNAT had no `timeout` parameter. If XNAT's container management API hangs (e.g., Docker daemon busy), the imaging-api worker blocks permanently.

**Fix**: Restart the imaging-api container (clears stuck state), and ensure the timeout fix is applied (see Section 3.3):

```bash
ssh flip-trust "docker restart trust1-imaging-api-1"
```

---

## 3. Application (Pipeline / API)

### 3.1 API returns 401/403 on all authenticated routes (MFA enforcement)

**Symptom**: All authenticated API calls return `401 Unauthorized` or `403 Forbidden`. The UI shows "Something went wrong" but the login works.

**Root cause**: `ENFORCE_MFA=true` (the Settings default in `flip-api/src/flip_api/config.py:85`) gates every authenticated route on TOTP enrollment. The production compose file (`deploy/compose.production.yml`) did not pass `ENFORCE_MFA`, so the default `true` always applied.

**Fix**: The compose file now passes `ENFORCE_MFA=${ENFORCE_MFA:-true}` (inheriting the secure default). To temporarily disable for testing, set in `.env.stag`:

```
ENFORCE_MFA=false
```

Then redeploy: `make deploy-centralhub PROD=stag`

---

### 3.2 FL status endpoints return empty / DNS resolution fails

**Symptom**: `/api/fl/status` returns empty responses. The flip-api logs show `Name or service not known` for `fl-api-net-1.flip.local:8000`.

**Root cause**: `NET_ENDPOINTS` is set to Service Discovery hostnames (e.g., `http://fl-api-net-1.flip.local:8000`) which only resolve in ECS Fargate with Cloud Map enabled. On EC2 with Docker Compose, use the Docker container name instead.

**Fix**: There are TWO places to fix:

1. **Environment file** (`.env.stag`): `NET_ENDPOINTS={"net-1":"http://flip-fl-api-net-1:8000"}`

2. **Database** (the `fl_nets` table caches the endpoint from first seed — updating env var alone isn't enough):

   ```bash
   ssh flip "docker exec flip-api python3 -c \"
   import asyncpg, os, json, boto3; import asyncio
   async def main():
       client = boto3.client('secretsmanager', region_name='eu-west-2')
       secret = client.get_secret_value(SecretId=os.environ['POSTGRES_SECRET_ARN'])
       pwd = json.loads(secret['SecretString']).get('password', '')
       conn = await asyncpg.connect(host=os.environ['DB_HOST'], port=5432, user=os.environ['POSTGRES_USER'], database=os.environ['POSTGRES_DB'], password=pwd)
       await conn.execute('UPDATE fl_nets SET endpoint = \$1 WHERE name = \$2', 'http://flip-fl-api-net-1:8000', 'net-1')
       await conn.close()
   asyncio.run(main())\"
   ```

---

### 3.3 Imaging pipeline stuck on "Awaiting creation..."

**Symptom**: Project is approved but the trust shows "Awaiting creation..." indefinitely. No images are imported to XNAT.

**Root cause**: Several interacting issues:

1. **`last_reimport` defaults to `now()`** (`main_models.py:235`): The `reimport_failed_studies` function checks `now > last_reimport + PROJECT_REIMPORT_RATE(60min)`. For a brand-new project, `last_reimport` was set to the creation time, so the check fails and no REIMPORT_STUDIES task is created for 60 minutes.

2. **Scheduler runs every 30 minutes** (`SCHEDULER_REIMPORT_IMAGING_PROJECT_STUDIES_RATE=30`): Even after the 60-minute cooldown, the next scheduler cycle may be up to 30 minutes away.

3. **trust-api default 30s timeout**: `make_request()` defaults to 30 seconds, but XNAT project creation can take longer.

4. **imaging-api XNAT calls lack timeouts**: `requests.get/post/put/delete` to XNAT without `timeout=` hang forever if XNAT is unresponsive.

**Fix** (already applied in PR #410):

- `task_handlers.py`: CREATE_IMAGING/REIMPORT_STUDIES now have 120s timeouts
- `projects.py`: All XNAT API calls now have 120s timeouts

**Temporary workaround** (force import for stuck projects):

```bash
# 1. Check if CREATE_IMAGING task failed
ssh flip "docker exec flip-api python3 -c '...'"

# 2. Reset to PENDING if FAILED
UPDATE trust_task SET status='PENDING' WHERE id='<task_id>';

# 3. If still failing, force by setting last_reimport far in the past:
UPDATE xnat_project_status SET last_reimport='2020-01-01' WHERE xnat_project_id='<id>';

# 4. Restart imaging-api if it's hung:
ssh flip-trust "docker restart trust1-imaging-api-1"
```

---

### 3.4 FL client cannot connect to FL server

**Symptom**: Trust `fl-client-net-1` logs show `cannot send to 'server': target_unreachable` or connection retries.

**Root cause**: The FL server container was restarted (e.g., during `deploy-centralhub`) and the client needs to re-establish the gRPC connection. The NVFLARE client retries automatically every 10 seconds. The FL server log should show `Re-activate the client: Trust_1`.

**Fix**: Usually self-healing. Verify the FL server is listening:

```bash
ssh flip "docker logs fl-server-net-1 --since 2m | grep -E 'Connection|re-activate|Client'"
```

---

### 3.5 Imaging API container hung (no responses)

**Symptom**: `CREATE_IMAGING` and `REIMPORT_STUDIES` tasks repeatedly fail with `ReadTimeout`. Direct XNAT API calls work, but the imaging-api endpoint hangs.

**Root cause**: The imaging-api made XNAT API calls without `timeout=` parameters. If XNAT's container management API (dcm2niix command enablement, event subscriptions) hung, the imaging-api worker thread blocked permanently. All subsequent requests queued behind the hung worker.

**Fix**: Restart the imaging-api container:

```bash
ssh flip-trust "docker restart trust1-imaging-api-1"
```

The code fix (adding `timeout=120` to all XNAT calls) prevents recurrence.

---

## 4. Configuration

### 4.1 `NET_ENDPOINTS` hostname not resolvable

**Symptom**: flip-api logs show `[Errno -2] Name or service not known` for `fl-api-net-1.flip.local:8000`.

**Root cause**: `NET_ENDPOINTS` points to Service Discovery FQDNs designed for ECS Fargate (PR 2). On EC2 with Docker Compose, containers communicate via Docker's built-in DNS using container names.

**Fix**: Set `NET_ENDPOINTS={"net-1":"http://flip-fl-api-net-1:8000"}` in `.env.stag` AND update the `fl_nets` table in the database (see Section 3.2).

---

### 4.2 Missing `ORTHANC_STORAGE_DIR_TRUST_1` env var

**Symptom**: `make deploy-trust` fails with Docker volume mount parse error.

**Fix**: Add `ORTHANC_STORAGE_DIR_TRUST_1=/opt/flip/orthanc/orthanc-storage` to `.env.stag`.

---

### 4.3 `ENFORCE_MFA` env var not passed to container

**Symptom**: All authenticated routes return 401/403 despite `ENFORCE_MFA=false` in `.env.stag`.

**Root cause**: The production compose file (`deploy/compose.production.yml`) did not include `ENFORCE_MFA` in the flip-api environment block. The Pydantic Settings default (`true`) was never overridden.

**Fix**: The compose file now passes `ENFORCE_MFA=${ENFORCE_MFA:-true}` so the env var can be overridden. To disable MFA in staging, add `ENFORCE_MFA=false` to `.env.stag` and redeploy. Note: the default in compose is `true` (secure by default) — you must explicitly set `false` to disable.

---

### 4.4 DHCP options change not applied to running instances

**Symptom**: After deploying the ECS foundation, `flip.local` domain resolution doesn't work from existing EC2 instances.

**Root cause**: The `dhcp.tf` resource associates a new DHCP options set (with `flip.local` search domain) to the VPC. Existing EC2 instances won't pick up the new options until their DHCP lease expires and renews (typically 24-72 hours for AWS default, or a reboot). Until renewal, the instances continue using the previous (default) DHCP options.

**Fix**: Reboot the EC2 instance to force an immediate DHCP renewal:
```bash
aws ec2 reboot-instances --instance-ids <instance-id> --profile FlipDeveloperAccess
```
Or wait for the lease to renew naturally. This only matters if the instance needs to resolve `flip.local` domains (which it doesn't during PR 1 — ECS Fargate tasks are the consumers in PR 2).

---

## 5. Verification Commands

### Quick health check

```bash
make status PROD=stag
```

### Check specific pipeline task status

```bash
ssh flip "docker exec flip-api python3 -c \"
import asyncpg, os, json, boto3, asyncio
async def main():
    client = boto3.client('secretsmanager', region_name='eu-west-2')
    secret = client.get_secret_value(SecretId=os.environ['POSTGRES_SECRET_ARN'])
    pwd = json.loads(secret['SecretString']).get('password','')
    conn = await asyncpg.connect(host=os.environ['DB_HOST'], port=5432, user=os.environ['POSTGRES_USER'], database=os.environ['POSTGRES_DB'], password=pwd)
    rows = await conn.fetch('SELECT task_type, status, created_at FROM trust_task WHERE trust_id = \$1 ORDER BY created_at DESC LIMIT 10', '<trust-id>')  # replace <trust-id> with the actual Trust UUID
    for r in rows: print(f'{r[0]:30s} {r[1]:12s} {r[2]}')
    await conn.close()
asyncio.run(main())\"
```

### Check for stuck XNAT projects (last_reimport within last hour, zero reimports)

```bash
ssh flip "docker exec flip-api python3 -c \"
SELECT xnat_project_id, last_reimport, reimport_count FROM xnat_project_status WHERE last_reimport > NOW() - INTERVAL '1 hour' AND reimport_count = 0;
\""
```

### Scan container logs for errors

```bash
# Central Hub
ssh flip "docker logs flip-api 2>&1 | grep -iE 'ERROR|Exception|Traceback' | tail -20"

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
ssh flip "docker logs fl-server-net-1 2>&1 | grep -E 'Client|Re-activate' | tail -5"
```
