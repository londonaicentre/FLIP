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

### 1.5 Trust EC2 runs out of disk during `make deploy-trust`

**Symptom**: `make deploy-trust` fails partway through `docker compose pull` with:

```
failed to extract layer ... torch/_inductor/codegen/cpp_micro_gemm.py:
no space left on device
```

`df -h /` on the trust EC2 shows the root volume near 100 % used, even though it was nominally 30+ GB free at the start of the pull.

**Root cause**: The full trust image set (NVFLARE `flare-fl-client` with multi-GB torch deps, XNAT Tomcat ~4 GB compressed, Orthanc, OMOP, observability stack, three trust APIs) blows through 50 GB of overlayfs snapshot space when extracted concurrently. The 50 GB default sized for the legacy compose stack is no longer adequate.

**Fix**: The trust EC2 module now provisions a 100 GB root volume (`modules/trust_ec2/main.tf`). Volume modification is in-place — no instance replacement — but the partition + filesystem still need an online resize once after `terraform apply`:

```bash
ssh flip-trust 'sudo growpart /dev/nvme0n1 1 && sudo resize2fs /dev/nvme0n1p1'
```

Then retry `make deploy-trust`. Verify with `df -h /` (should show ~100 GB).

---

### 1.6 AWS SSO token expired

**Symptom**: All `aws` commands return `Unable to locate credentials` or `AccessDenied`.

**Fix**:

```bash
aws sso login --profile FlipDeveloperAccess-080369786334 --use-device-code
```

Use `--use-device-code` for headless/SSH environments.

---

### 1.7 ALB returns 502 / NLB shows unhealthy targets after ECS cutover

**Symptom**: After bringing ECS Fargate up alongside the legacy EC2 stack, the ALB still returns 502 for `/api/*` and the NLB target group shows the legacy EC2 target as unhealthy. Trust-side fl-client cannot reach fl-server. trust-api heartbeats may also return 401 because they hit the legacy flip-api whose secret hashes are stale.

**Root cause**: The ALB listener rule and NLB TCP listener still forward to the EC2-instance target groups. Adding ECS services without `load_balancer` blocks does not register the task ENI IPs anywhere — the LBs keep routing to the now-stopped EC2 containers.

**Fix** (already in place on `develop`): in `main.tf`, ECS task IPs are registered to dedicated `target_type = "ip"` target groups (`ecs_flip_api`, `ecs_fl_server_tcp`) and the listener rules / default actions point at them. The `ecs_services.tf` services include `load_balancer` blocks so each running task auto-registers. The legacy module-managed `ec2-instance-fl-server-tcp` target group is removed from the NLB module config so it stops registering an unhealthy EC2 target.

If you regress this: confirm the ALB listener rule's `target_group_arn` is `aws_lb_target_group.ecs_flip_api.arn` and the NLB TCP listener default action targets `aws_lb_target_group.ecs_fl_server_tcp.arn`. `target_type = "ip"` is mandatory for awsvpc Fargate — `instance` will not register.

---

### 1.8 ECS-internal call from fl-api → fl-server hangs in `try_connect`

**Symptom**: On ECS `fl-api-net-1` boots, registers in Cloud Map, but its NVFLARE admin client hangs trying to reach fl-server. `fl-server-net-1` is healthy on the NLB and reachable from the trust EC2.

**Root cause**: fl-api is the NVFLARE admin client and connects directly to fl-server inside the VPC (the NLB path is reserved for off-VPC trust FL clients). The fl-server SG had no ingress on 8002 from the fl-api SG, so the in-VPC connect attempt timed out.

**Fix**: `ecs_sg.tf` now opens fl-server SG ingress on 8002 from the fl-api SG. If you add another in-VPC NVFLARE admin consumer, add its SG to the same ingress rule.

---

### 1.9 Verifying which FL image an ECS task pulled — GuardDuty sidecar digest trap

**Symptom**: After a `force-new-deployment` of `fl-server-net-1` / `fl-api-net-1`, you check the running task's image digest against the GHCR tag to confirm the new build is live. One container's `imageDigest` does **not** match the GHCR `:stag`/`:prod` manifest, and worse, querying GHCR for that digest returns **HTTP 404** (it does not exist in GHCR at all). Looks like the task is running a stale/unknown image.

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

### 2.1 Docker volume mount parse failure (`empty section between colons`)

**Symptom**: `make deploy-trust` fails with:

```
invalid spec: :/var/lib/orthanc/db: empty section between colons
```

**Root cause**: `ORTHANC_STORAGE_DIR` is missing from the per-trust kit file
(`trust/.env.<KIT>`), so the Orthanc compose mount expands to an empty host
path. The compose files (`trust/deploy/compose_trust.{development,production}.yml`)
both consume the unsuffixed `${ORTHANC_STORAGE_DIR}` — the older
`_TRUST_{1,2}` suffixed names from `.env.stag` were retired by the
per-trust-kit refactor.

**Fix**: Set the host-local profile entry in the kit file:

```
ORTHANC_STORAGE_DIR=/opt/flip/orthanc/orthanc-storage-trust1
```

The trust kit `.example` templates carry this key in their **Host-local
profile** section; if you bootstrapped a kit before the refactor, copy the
key from a current template (e.g. `trust/.env.GSTT.development.example` or the
base `trust/.env.example`) and adjust the path for the trust host.

---

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

### 2.5 NVFLARE participant kit fails `LoadResult.INVALID_SIGNATURE` on fl-api / fl-server boot

**Symptom**: After regenerating an NVFLARE participant kit and redeploying, fl-api or fl-server crash-loops with `LoadResult.INVALID_SIGNATURE`. Or fl-client connects to fl-server but TLS handshake fails with `SSLV3_ALERT_BAD_CERTIFICATE` / `CERTIFICATE_VERIFY_FAILED`.

**Root cause**: NVFLARE signs every file in the kit at provision time (`signature.json` references a hash of `fed_admin.json`, `rootCA.pem`, `client.crt`, etc.). Two failure modes:

1. **In-place edits**: Sed-patching `fed_admin.json` after the kit lands (e.g. to swap a hostname) tears the signature.
2. **Stale `aws s3 sync`**: `aws s3 sync` skips files when source and destination have similar size + mtime, so a freshly regenerated kit can leave a stale `rootCA.pem` / `client.crt` / `fed_client.json` next to a fresh `signature.json` (or vice versa). The on-disk kit is then signed by one rootCA but presents a cert from another.

**Fix**:

1. Never edit the kit after provision. If a hostname needs to change, regenerate the kit with the right hostname baked in.
2. The EFS provisioner (`ecs_efs_provision.tf`) and the trust Ansible playbook (`site.yml`, both NVFLARE Trust_1 and Flower trust syncs) now wipe the destination with `find <dest> -mindepth 1 -delete` immediately before the `aws s3 sync` / `aws s3 cp`. This forces a clean copy. If you script your own kit sync somewhere new, mirror this pattern.

To recover a stuck stack: re-run `make full-deploy` (central hub) or `make deploy-trust` (trust EC2). Both now wipe before sync.

---

### 2.6 fl-client returns 401 from data-access-api or imaging-api

**Symptom**: A new model run fails with:

```
requests.exceptions.HTTPError: 401 Client Error: Unauthorized for url: http://data-access-api:8000//cohort/dataframe
```

(or the same on `imaging-api`). Trust-api → data-access-api / imaging-api calls succeed; only fl-client calls fail.

**Root cause**: The running fl-client / fl-server / fl-api images predate the change that added `headers=_trust_internal_headers()` to every outbound `flip.get_dataframe` / `flip.get_by_accession_number` / `flip.add_resource` call. Without that header, the data-access-api / imaging-api router-level auth check rejects the request with 401.

**Fix**: bump `DOCKER_FL_TAG` in `.env.stag` (and `.env.production` for prod) to an FL image build that includes the trust-internal-header change, then redeploy. A single tag bump rolls all three images:

```bash
# Pick an FL image tag (a develop SHA) that includes the trust-internal-header change
sed -i 's/^DOCKER_FL_TAG=.*/DOCKER_FL_TAG=<sha>/' .env.stag

# fl-api + fl-server (ECS task defs read TF_VAR_flip_fl_image_tag)
make apply PROD=stag

# fl-client (trust EC2 compose pulls the same tag)
make deploy-trust PROD=stag
```

Verify the running fl-client has the header wiring:

```bash
ssh flip-trust 'docker exec trust1-fl-client-net-1 grep -n "_trust_internal_headers" /app/flip/core/standard.py'
```

You should see calls at `get_dataframe`, `get_by_accession_number`, and `add_resource`.

---

### 2.7 Trust EC2 Orthanc is empty — all image pulls go straight to QueueFailed

**Symptom**: Every image pull on the EC2 trust shows `QueueFailed=<all>,
Queued=0, Processing=0, Successful=0` immediately after project approval.
imaging-api logs show `POST /xapi/dqr/query/studies` returning **204** (no
content) for every accession — the PACS C-FIND finds nothing. The trust's
XNAT `dqr.log` is silent (nothing was ever queued).

**Diagnosis**:

```bash
# Orthanc study count — should be ~4187 for the mock trust1 dataset
ssh flip-trust 'docker exec trust1-imaging-api-1 python3 -c "
import httpx
print(httpx.get(\"http://orthanc:8042/statistics\", auth=(\"admin\",\"admin\"), timeout=5).json())"'
# CountStudies: 0  →  Orthanc has no data

# Where does Orthanc's data dir actually point?
ssh flip-trust 'docker inspect trust1-orthanc-1 --format "{{json .Mounts}}"' | python3 -m json.tool
```

**Root cause**: Compose interpolates the Orthanc bind path
(`ORTHANC_STORAGE_DIR*` from the kit file / env) **at deploy time on the
machine driving the deploy**. If the deploy was driven from an admin
workstation, the workstation's local path (e.g.
`/home/<user>/.../trust/orthanc/orthanc-storage-trust1`) gets baked into the
remote compose stack; Docker auto-creates that directory **empty** on the EC2
host, and Orthanc starts with zero studies. The `up-trust` target's
`update-orthanc-data` prereq only populates the dir on the machine where it
runs — not on the EC2.

**Fix (stopgap — seed the live bind dir on the EC2):**

```bash
ssh flip-trust
DIR=$(docker inspect trust1-orthanc-1 --format '{{range .Mounts}}{{if eq .Destination "/var/lib/orthanc/db"}}{{.Source}}{{end}}{{end}}')
# Version from trust/orthanc/.data_version (e.g. 20260106); trust slot from the kit (trust1/trust2)
curl -fSL -o /tmp/orthanc-data.tar \
  "https://huggingface.co/datasets/aicentreflip/trust-data/resolve/main/trust1/trust1_orthanc_data_20260106.tar"
docker stop trust1-orthanc-1
sudo rm -rf "$DIR"/*          # wipe the stale empty index
sudo tar xf /tmp/orthanc-data.tar -C "$DIR"
docker start trust1-orthanc-1
rm /tmp/orthanc-data.tar
```

Verify a cohort accession is findable, then re-trigger the pull from the UI
(re-import button):

```bash
docker exec trust1-imaging-api-1 python3 -c "
import httpx
r = httpx.post('http://orthanc:8042/tools/find', auth=('admin','admin'),
               json={'Level':'Study','Query':{'AccessionNumber':'<an accession from OMOP>'},'Limit':1}, timeout=15)
print('found:', len(r.json()) > 0)"
```

**Fix (proper)**: Set host-appropriate data dirs in the kit file's Host-local
profile section and run the data seeding **on the trust host** (the
`update-orthanc-data` flow), so deploys never inherit the admin workstation's
paths. Audit the other bind mounts on the host for the same leak —
`docker inspect <container> --format '{{json .Mounts}}'` per container.

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

**Root cause**: Central Hub services run on ECS Fargate, where the FL API is resolved through Cloud Map. A stale Docker Compose hostname such as `flip-fl-api-net-1` cannot resolve there.

**Fix**: Set the Cloud Map endpoint in `.env.stag`:

```text
NET_ENDPOINTS={"net-1":"http://fl-api-net-1.flip.local:8000"}
```

Then run `make plan PROD=stag`, `make apply PROD=stag`, and `make deploy-centralhub PROD=stag`. The flip-api startup seed reconciles the `fl_nets` row to `NET_ENDPOINTS`; a manual database update is neither required nor durable.

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
# Connect with psql using the bastion recipe in §5, then check the task.
SELECT id, task_type, status, created_at
FROM trust_task
WHERE task_type = 'CREATE_IMAGING'
ORDER BY created_at DESC
LIMIT 10;

UPDATE trust_task SET status='PENDING' WHERE id='<task_id>';

UPDATE xnat_project_status SET last_reimport='2020-01-01' WHERE xnat_project_id='<id>';
```

If imaging-api is hung, restart it on the Trust EC2:

```bash
ssh flip-trust "docker restart trust1-imaging-api-1"
```

Related states with different causes:

- **`QueueFailed` for every accession right after approval** → the PACS C-FIND
  finds nothing; usually an empty Orthanc on the trust host — see §2.7.
- **Stuck on "Processing" with no progress, and re-import logs
  `No studies to retry import`** → executed PACS request rows pin the status
  (imaging-api classifies any accession with an executed row as Processing,
  regardless of row status); the rows must be deleted before re-import works —
  see §2.4 "Forcing a Re-pull" in
  `deploy/providers/kubernetes/TROUBLESHOOTING.md` (same procedure on EC2 via
  `docker exec` into the xnat-db container).

---

### 3.4 FL client cannot connect to FL server

**Symptom**: Trust `fl-client-net-1` logs show `cannot send to 'server': target_unreachable` or connection retries.

**Root cause**: The FL server container was restarted (e.g., during `deploy-centralhub`) and the client needs to re-establish the gRPC connection. The NVFLARE client retries automatically every 10 seconds. The FL server log should show `Re-activate the client: Trust_1`.

**Fix**: Usually self-healing. Verify the FL server is listening:

```bash
AWS_PROFILE=stag aws logs tail /ecs/fl-server-net-1 --since 2m | grep -E 'Connection|re-activate|Client'
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

### 3.6 FL training aborts with `num_samples=0` (looks like a data bug)

**Symptom**: A model run reaches the FL training stage and immediately dies with:

```
ValueError: num_samples should be a positive integer value, but got num_samples=0
```

The cohort query returned a non-empty dataframe (visible in trust-api logs), so this looks like a preprocessing or trainer bug at first glance.

**Root cause** (the one we hit; confirm with logs before assuming): every `imaging-api/download/images/net-1` call from fl-client returned 500 with `[Errno 13] Permission denied: '/app/data/images/net-1'` and `Found 0 files in total`. The bind-mount source on the trust EC2 (`/opt/flip/data/trust-1` / `trust-2`) was owned by root because Docker auto-created it as root when no Ansible play pre-created it. imaging-api runs as uid 1000 (`flip`) and could not `mkdir` inside.

**Diagnosis**: pull the fl-client log directly from the workspace, then cross-check imaging-api:

```bash
ssh flip-trust 'docker exec trust1-fl-client-net-1 \
  find /app -name "log.txt" -path "*<run-id>*" -exec tail -200 {} \;'

ssh flip-trust 'docker logs trust1-imaging-api-1 --since 10m 2>&1 | \
  grep -iE "permission|errno|500"'
```

If you see `Permission denied` on `/app/data/images/...`, you have the same bug.

**Fix** (sticky): `site.yml` now pre-creates `/opt/flip/data/trust-1` and `/opt/flip/data/trust-2` owned by `ubuntu:ubuntu` (uid 1000 inside the containers) and removes the unused `/opt/flip/data/images` entry. Re-run `make full-deploy PROD=stag`.

**Hot-fix** (existing host, no redeploy):

```bash
ssh flip-trust 'sudo chown -R 1000:1000 /opt/flip/data/trust-1 /opt/flip/data/trust-2'
```

The general principle: anywhere a non-root container bind-mounts a host path, pre-create the host path with the right uid in Ansible. Letting Docker auto-create it leaves a root-owned source that silently breaks every non-root container that touches it.

---

### 3.7 Single net permanently starved of new training jobs

**Symptom**: One FL net never picks up new training jobs even though scheduler logs show jobs being queued. Other nets are fine.

**Root cause**: `FLScheduler` rows transition to `BUSY` while a job runs. If `prepare_and_start_training` raised mid-flight in a previous cycle, the row was left `BUSY` with no live job — `run_jobs_core` then refused to schedule new work for that net forever.

**Fix** (already in place on `develop`): `_recover_stale_busy_schedulers()` runs at the top of every `run_jobs_core` cycle and resets `BUSY` rows whose associated job no longer exists. No manual intervention needed; just confirm the next cycle clears the row.

---

## 4. Configuration

### 4.1 `NET_ENDPOINTS` hostname not resolvable

**Symptom**: flip-api logs show `[Errno -2] Name or service not known` for `fl-api-net-1.flip.local:8000`.

**Root cause**: `NET_ENDPOINTS` points to Service Discovery FQDNs designed for ECS Fargate (PR 2). On EC2 with Docker Compose, containers communicate via Docker's built-in DNS using container names.

**Fix**: Set `NET_ENDPOINTS={"net-1":"http://flip-fl-api-net-1:8000"}` in `.env.stag` AND update the `fl_nets` table in the database (see Section 3.2).

---

### 4.2 Missing `ORTHANC_STORAGE_DIR` env var

**Symptom**: `make deploy-trust` fails with Docker volume mount parse error.

**Fix**: Add `ORTHANC_STORAGE_DIR=/opt/flip/orthanc/orthanc-storage` to the
per-trust kit file `trust/.env.<KIT>` (see 2.1 for the full root-cause
context — the compose files consume the unsuffixed name and read from the
kit, not `.env.stag`).

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

### Connect to RDS through the Central Hub bastion

The bastion has `psql` installed and its security group is allowed to reach RDS on port 5432. It deliberately has no Secrets Manager permission, so obtain the database password through the approved operator channel and enter it only at the interactive prompt.

```bash
cd deploy/providers/AWS
make ssh-config PROD=stag
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
