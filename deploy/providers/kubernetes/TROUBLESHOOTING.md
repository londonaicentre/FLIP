# Troubleshooting Guide — FLIP K8s Trust

This guide covers common issues and resolutions for the Kubernetes-deployed trust
services, with a focus on the XNAT DICOM import pipeline.

---

## Table of Contents

1. [Pods Not Starting](#1-pods-not-starting)
2. [XNAT DICOM Import Pipeline Issues](#2-xnat-dicom-import-pipeline-issues)
   - [2.1 PacsNotStorableException — destination AE/port mismatch](#21-pacsnotstorableexception--destination-aeport-mismatch)
   - [2.2 Studies Received but Land in the Unassigned Prearchive](#22-studies-received-but-land-in-the-unassigned-prearchive)
   - [2.3 imaging-api Gets 401 from XNAT (flipServiceAccount)](#23-imaging-api-gets-401-from-xnat-flipserviceaccount)
   - [2.3a dcm2niix Never Registered (admin missing ContainerManager)](#23a-dcm2niix-never-registered-admin-missing-containermanager)
   - [2.4 Forcing a Re-pull (status stuck on "Processing")](#24-forcing-a-re-pull-status-stuck-on-processing)
   - [2.5 Running the Imaging Import Worker Manually](#25-running-the-imaging-import-worker-manually)
   - [2.6 C-MOVE Testing from the DCMTK Pod](#26-c-move-testing-from-the-dcmtk-pod)
   - [2.7 Checking DICOM Connectivity](#27-checking-dicom-connectivity)
3. [OMOP Data Issues](#3-omop-data-issues)
4. [Trust Registration and Heartbeat](#4-trust-registration-and-heartbeat)
5. [XNAT HTTPS Issues](#5-xnat-https-issues)
6. [Debug Scripts](#6-debug-scripts)
7. [FL Client / FL Server Connection Issues](#7-fl-client--fl-server-connection-issues)
   - [7.1 Network Policy Blocking Egress to Port 8002](#71-network-policy-blocking-egress-to-port-8002)
   - [7.2 Certificate Key Usage BIT_INCORRECT](#72-certificate-key-usage-bitincorrect)
   - [7.3 Missing SAN in Server Certificate](#73-missing-san-in-server-certificate)
   - [7.4 Signature PSS Padding Mismatch](#74-signature-pss-padding-mismatch)
   - [7.5 EFS File Permission Lost (sub_start.sh)](#75-efs-file-permission-lost-sub_startsh)
   - [7.6 Entrypoint Background-Process Exit (start.sh)](#76-entrypoint-background-process-exit-startsh)
   - [7.7 AWS Credentials Expired in K8s Secret](#77-aws-credentials-expired-in-k8s-secret)
   - [7.8 EFS Sync Permission Issues (Root vs UID 1001)](#78-efs-sync-permission-issues-root-vs-uid-1001)
   - [7.9 Container Image Pull from Private ECR](#79-container-image-pull-from-private-ecr)
   - [7.10 gRPC Async Connect Fails on Kernel 7 (Ubuntu 26.04)](#710-grpc-async-connect-fails-on-kernel-7-ubuntu-2604)

---

## 1. Pods Not Starting

### Check pod status

```bash
kubectl get pods -n flip-trust
kubectl describe pod <pod-name> -n flip-trust
kubectl logs <pod-name> -n flip-trust
kubectl logs <pod-name> -n flip-trust -c <init-container-name>
```

### Common Causes

| Symptom | Likely Cause | Fix |
|---------|------------|-----|
| Pod stuck in `Init:0/1` | Init container 0% complete. Check init container logs. | `kubectl logs <pod> -n flip-trust -c <init-container>` |
| Pod stuck in `Init:1/2` | Second init container failed. | Check download-plugins or wait-for-xnat-db init logs |
| `CrashLoopBackOff` | Container exits immediately. Check logs, check env vars. | `kubectl logs <pod> -n flip-trust` |
| `Pending` (PVC) | PVC not bound (check storage class) | `kubectl get pvc -n flip-trust` |
| `Pending` (resources) | Insufficient CPU/memory | `kubectl describe pod <pod> -n flip-trust` — check Events |

### PVC Storage Issues

The default chart uses the cluster's default `StorageClass`. On **k3s** (local-path-provisioner),
only `ReadWriteOnce` is supported. Override `accessMode` via values override file:

```yaml
sharedImagesPvc:
  accessMode: ReadWriteOnce

xnat:
  nginx:
    persistence:
      accessMode: ReadWriteOnce
```

Apply with:
```bash
helm upgrade trust-release deploy/providers/kubernetes -n flip-trust \
  -f deploy/providers/kubernetes/values.yaml \
  -f deploy/providers/kubernetes/k8s-trust-k3s-overrides.yaml \
  --set imageTag=stag
```

---

## 2. XNAT DICOM Import Pipeline Issues

### 2.1 PacsNotStorableException — destination AE/port mismatch

**Symptom:** Every DQR dequeue cycle (~6 min apart) logs
`PacsNotStorableException: null` at
`BasicDicomQueryRetrieveService.java:320`, even though the PACS (Orthanc) is
correctly configured with `storable=true` in the XNAT database. Orthanc logs
show zero incoming DICOM — the request fails **before any DICOM connection**.

**Check DQR logs:**
```bash
XNAT_POD=$(kubectl get pods -n flip-trust -l app.kubernetes.io/component=xnat-web -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n flip-trust "$XNAT_POD" -- tail -30 /data/xnat/home/logs/dqr.log
```

**Root Cause:** The exception name is misleading — it is **not** about the
PACS's `storable` flag. Decompiling the plugin (`javap -c` on
`dicom-query-retrieve-*.jar` from `/data/xnat/home/plugins/`) shows the actual
check in `importFromPacsRequest`:

```java
String aeAndPort = request.getDecodedAeAndPort();   // e.g. "XNAT:8080"
if (!pacs.isDicomWebEnabled() && !doesScpReceiverExist(aeAndPort)) {
    throw new PacsNotStorableException(new AeTitle(aeAndPort));
}
```

`doesScpReceiverExist` does an **exact `"AE:port"` string match** against the
configured DICOM SCP receivers. The destination AE on each queued request is
built by imaging-api as `XNAT:{XNAT_PORT}` (`ImportStudyRequest.port` in
`imaging_api/routers/schemas.py`). **`XNAT_PORT` is therefore the DICOM SCP
receiver port (8104), NOT XNAT's web port (8080).** If the chart sets
`imagingApi.env.XNAT_PORT: "8080"`, every request carries destination
`XNAT:8080`, no receiver matches, and the dequeue throws.

**Diagnosis:**

```bash
# 1. What destination do the queued requests carry?
kubectl exec -n flip-trust trust-release-flip-trust-xnat-db-0 -- psql -U xnat -d xnat -c \
  "SELECT DISTINCT destination_ae_title FROM xhbm_queued_pacs_request;"

# 2. What receivers exist?
kubectl exec -n flip-trust "$XNAT_POD" -- \
  curl -s -u admin:<admin-pass> http://localhost:8080/xapi/dicomscp/
# Compare: destination_ae_title must equal "<aeTitle>:<port>" of a receiver.
```

**Fix:**

1. Set `XNAT_PORT: "8104"` in `values.yaml` (`imagingApi.env`) — already the
   chart default since 2026-06-05 — or patch the live ConfigMap and restart:
   ```bash
   kubectl patch configmap trust-release-flip-trust-imaging-api -n flip-trust \
     --type merge -p '{"data":{"XNAT_PORT":"8104"}}'
   kubectl rollout restart deployment/trust-release-flip-trust-imaging-api -n flip-trust
   ```
2. Already-queued requests keep their wrong destination — fix them in place
   (DQR retries FAILED queued requests automatically, up to
   `dqrMaxPacsRequestAttempts`):
   ```bash
   kubectl exec -n flip-trust trust-release-flip-trust-xnat-db-0 -- psql -U xnat -d xnat -c \
     "UPDATE xhbm_queued_pacs_request SET destination_ae_title = 'XNAT:8104'
      WHERE destination_ae_title = 'XNAT:8080';"
   ```

### 2.2 Studies Received but Land in the Unassigned Prearchive

**Symptom:** C-MOVE works (executed PACS requests reach status `RECEIVED`,
Orthanc shows outgoing DICOM), but no sessions appear in the project. DQR logs:

```
WARN ... PacsDequeueThread - Cannot build session. 0 prearchive sessions found
for study 1.2.826... in project <imaging-project-id>.
```

and `/data/xnat/prearchive/` fills with **top-level timestamp directories**
(`20260605_101702647/...`) instead of `<project>/<timestamp>/...`.

**Root Cause:** The DICOM SCP receiver is the stock default
(`identifier: dicomObjectIdentifier`, `customProcessing: false`). Project
routing and the DQR relabel map (Subject→UUID, Session→accession number — what
fl-client lookups depend on) are applied by the **DQR plugin's receiver
identifier**. Without it, received studies cannot be matched to the requesting
project and fall into *Unassigned*.

**Fix:** Replace the receiver with the DQR-aware one (what the Compose deploy's
`trust/xnat/xnat/config/configure-xnat.sh` does, and what the chart's
`xnat-init-job.yaml` now does on every install/upgrade):

```bash
# Delete the stock receiver (find its id via GET /xapi/dicomscp/)
kubectl exec -n flip-trust "$XNAT_POD" -- \
  curl -s -X DELETE -u admin:<admin-pass> http://localhost:8080/xapi/dicomscp/<id>

# Create the DQR receiver
kubectl exec -n flip-trust "$XNAT_POD" -- \
  curl -s -X POST -u admin:<admin-pass> -H "Content-Type: application/json" \
  http://localhost:8080/xapi/dicomscp -d '{
    "aeTitle": "XNAT", "port": 8104, "enabled": true,
    "customProcessing": true, "directArchive": true,
    "identifier": "dqrObjectIdentifier", "anonymizationEnabled": true,
    "whitelistEnabled": false, "routingExpressionsEnabled": false
  }'
```

Also apply + enable the site-wide anonymization script
(`trust/xnat/xnat/config/anon_script.das`) — the init job does this too:

```bash
kubectl cp trust/xnat/xnat/config/anon_script.das flip-trust/"$XNAT_POD":/tmp/anon_script.das
kubectl exec -n flip-trust "$XNAT_POD" -- sh -c '
  curl -s -X PUT -u admin:<admin-pass> -H "Content-Type: text/plain" \
    --data-binary @/tmp/anon_script.das http://localhost:8080/xapi/anonymize/site
  curl -s -X PUT -u admin:<admin-pass> -H "Content-Type: application/json" \
    -d true http://localhost:8080/xapi/anonymize/site/enabled'
```

Studies already stranded in the Unassigned prearchive were received **without**
the relabel map — don't archive them manually; delete them
(`rm -rf /data/xnat/prearchive/2026*` for top-level timestamp dirs only) and
re-pull (see §2.4).

> **Note:** With `directArchive: true`, the
> `Cannot build session. 0 prearchive sessions found` warning still appears on
> each pull but is **benign** — sessions bypass the prearchive and archive
> directly. Confirm with
> `ls /data/xnat/archive/<imaging-project-id>/arc001 | wc -l` or the project's
> experiment count.

### 2.3 imaging-api Gets 401 from XNAT (flipServiceAccount)

**Symptom:** `create_imaging` tasks fail instantly; trust-api logs
`External API error 401 for POST http://imaging-api:8000/projects/create-project-from-central-hub-project`
with an XNAT Tomcat 401 page (`Your login attempt failed...`) in the body. The
hub marks the task FAILED.

**Root Cause:** The XNAT init job creates `flipServiceAccount` at the DB layer
with a **fixed bcrypt hash** that does not correspond to the
`xnat-service-password` value in the chart Secret (which is what imaging-api
sends as `XNAT_SERVICE_PASSWORD`). Repeated failures also lock the account for
1 hour (20-attempt lockout).

**Fix:** Sync the password via the REST API (idempotent; the chart's
`xnat-init-job.yaml` `configure-xnat-web` container now does this on every
install/upgrade):

```bash
kubectl exec -n flip-trust "$XNAT_POD" -- \
  curl -s -X PUT -u admin:<admin-pass> -H "Content-Type: application/json" \
  http://localhost:8080/xapi/users/flipServiceAccount \
  -d '{"password": "<xnat-service-password from the chart Secret>"}'
```

Verify: `curl -u flipServiceAccount:<pass> http://localhost:8080/xapi/users/flipServiceAccount`
from inside the pod should return 200.

### 2.3a dcm2niix Never Registered (admin missing ContainerManager)

**Symptom:** Studies pull and archive fine, but no NIfTI is ever produced, so
FL training sees `num_samples=0`. `GET /xapi/commands?name=dcm2niix` returns
`[]` and `GET /xapi/docker/server` 500s — yet the `xnat-init` Job reported
**success**.

**Root Cause:** Container Service >= 3.7.0 requires the `ContainerManager` role
for `/xapi/docker/server` and `/xapi/commands`. The Compose deploy's
`trust/xnat/xnat/config/configure-xnat.sh` grants it to the admin account; the
chart's init job did not, so those two calls returned 401/403. Because every
call in the `configure-dcm2niix` container was `|| true` (or warn-only), the
Job still exited 0 with dcm2niix silently unregistered — the same
silent-failure class as FLIP#822 / FLIP#862.

**Fix:** Both are now handled by `xnat-init-job.yaml`: `configure-xnat-web`
grants the role, and `configure-dcm2niix` waits for it and then fails loudly
rather than swallowing the error. To repair a cluster configured by an older
chart:

```bash
kubectl exec -n flip-trust "$XNAT_POD" -- \
  curl -s -X PUT -u admin:<admin-pass> -H "accept: application/json" \
  http://localhost:8080/xapi/users/admin/roles/ContainerManager
```

Then re-run the init job (`helm upgrade` re-fires the post-install hook).
Verify: `curl -s -u admin:<pass> http://localhost:8080/xapi/users/admin/roles/`
must include `ContainerManager`, and
`curl -s -u admin:<pass> "http://localhost:8080/xapi/commands?name=dcm2niix"`
must return a command with a `dcm2niix-scan` wrapper.

### 2.4 Forcing a Re-pull (status stuck on "Processing")

**Symptom:** A pull went wrong (e.g. §2.2's unrouted studies), the executed
PACS requests sit at `RECEIVED`/`FAILED`, and the FLIP UI shows the trust
stuck on "Processing" forever. Re-clicking reimport logs
`No studies to retry import for project ...`.

**Root Cause:** imaging-api's `get_import_status` classifies an accession as
**Processing whenever ANY executed PACS request row exists for it — regardless
of that row's status** — and the reimport path
(`retry_retrieve_images_for_project`) only re-queues accessions classified
`Failed`/`QueueFailed`. Executed rows therefore pin the status and block the
retry.

**Fix:** Delete the executed rows (child table first), then trigger the
reimport:

```bash
# 1. Drop the stale executed requests for the imaging project
kubectl exec -n flip-trust trust-release-flip-trust-xnat-db-0 -- psql -U xnat -d xnat -c "
DELETE FROM xhbm_executed_pacs_request_series_ids WHERE executed_pacs_request IN
  (SELECT id FROM xhbm_executed_pacs_request WHERE xnat_project='<imaging-project-id>');
DELETE FROM xhbm_executed_pacs_request WHERE xnat_project='<imaging-project-id>';"

# 2. Trigger the reimport — from the hub UI (re-import button), or directly:
IMAGING_POD=$(kubectl get pods -n flip-trust -l app.kubernetes.io/component=imaging-api -o jsonpath='{.items[0].metadata.name}')
IKEY=$(kubectl get secret trust-release-flip-trust-secrets -n flip-trust \
  -o jsonpath='{.data.trust-internal-service-key}' | base64 --decode)
# encoded_query = base64url of the cohort SQL (visible decoded in imaging-api logs)
kubectl exec -n flip-trust "$IMAGING_POD" -- python3 -c "
import httpx
r = httpx.put('http://localhost:8000/retrieval/reimport_imaging_project_studies/<imaging-project-id>',
              params={'encoded_query': '<encoded-query>'},
              headers={'X-Trust-Internal-Service-Key': '$IKEY'}, timeout=30)
print(r.status_code, r.text)"
```

Expect `202 {"message": "Reimport queued", ...}`, then
`All studies queued successfully` in the imaging-api logs, and archived
sessions a few minutes later.

### 2.5 Running the Imaging Import Worker

> **Note:** The worker was originally written as a workaround when
> `PacsNotStorableException` was believed to be a DQR plugin bug. The real
> cause was the destination AE/port mismatch (§2.1) — with §§2.1–2.3 applied,
> the DQR pipeline works end-to-end and the worker is **not needed** for
> normal operation. It remains useful as a diagnostic fallback for moving
> studies when DQR is unavailable, but it bypasses the DQR relabel map, so
> sessions it imports are **not** relabelled (Subject UUID / Session=accession).

The Imaging Import Worker is a Kubernetes Job that:
1. Reads QUEUED PACS requests from the XNAT database
2. C-FINDs Orthanc to locate each study
3. C-MOVEs each study directly from Orthanc to XNAT
4. Updates the XNAT database to reflect completion

#### Enable the Worker

The worker is disabled by default. Enable it in your values override:

```yaml
imagingImportWorker:
  enabled: true
  runOnce: true
  batchSize: 100
  pacsId: 1
```

Then deploy:
```bash
helm upgrade trust-release deploy/providers/kubernetes -n flip-trust \
  -f deploy/providers/kubernetes/values.yaml \
  -f deploy/providers/kubernetes/k8s-trust-Trust_K8s.yaml \
  --set imageTag=stag \
  --set imagingImportWorker.enabled=true
```

#### Monitor the Worker

```bash
# Check Job status
kubectl get jobs -n flip-trust -l app.kubernetes.io/component=imaging-import-worker

# View logs
WORKER_POD=$(kubectl get pods -n flip-trust -l job-name -o jsonpath='{.items[0].metadata.name}')
kubectl logs -n flip-trust "$WORKER_POD" --tail=50

# Delete after completion (clean up)
kubectl delete job -n flip-trust trust-release-flip-trust-imaging-import-worker
```

#### Manual Run (one-off from the dcmtk pod)

The worker can also run directly from the DCMTK diagnostic pod:

```bash
# Copy the script to the pod
dcmtk_pod=$(kubectl get pods -n flip-trust -l run=dcmtk -o jsonpath='{.items[0].metadata.name}')
kubectl cp deploy/providers/kubernetes/scripts/imaging-import-worker.py \
  "$dcmtk_pod":/tmp/imaging-import-worker.py

# Install dependencies
kubectl exec -n flip-trust "$dcmtk_pod" -- pip install pynetdicom psycopg2-binary -q

# Run with custom settings
kubectl exec -n flip-trust "$dcmtk_pod" -- \
  python3 /tmp/imaging-import-worker.py

# Or adjust batch size and logging:
kubectl exec -n flip-trust "$dcmtk_pod" -- \
  BATCH_SIZE=150 LOG_LEVEL=DEBUG python3 /tmp/imaging-import-worker.py
```

### 2.6 C-MOVE Testing from the DCMTK Pod

The DCMTK diagnostic pod (`dcmtk`) contains `movescu`, `findscu`, and `echoscu`
for manual DICOM testing.

#### Test DICOM Connectivity

```bash
dcmtk_pod=$(kubectl get pods -n flip-trust -l run=dcmtk -o jsonpath='{.items[0].metadata.name}')

# C-ECHO to Orthanc
kubectl exec -n flip-trust "$dcmtk_pod" -- echoscu orthanc 4242

# C-ECHO to XNAT (DICOM SCP on port 8104)
kubectl exec -n flip-trust "$dcmtk_pod" -- echoscu xnat-web 8104
```

Both should respond successfully. If C-ECHO to XNAT fails, the SCP receiver
is not running correctly.

#### Manual C-FIND (find studies on Orthanc)

```bash
kubectl exec -n flip-trust "$dcmtk_pod" -- \
  findscu -aet DCMTK -aec ORTHANC orthanc 4242 \
    -k "QueryRetrieveLevel=STUDY" \
    -k "StudyInstanceUID=" \
    -k "AccessionNumber=" \
    -k "PatientName=" \
    -k "StudyDescription=" \
    -k "StudyDate="
```

Count studies:
```bash
kubectl exec -n flip-trust "$dcmtk_pod" -- bash -c '
  findscu -aet DCMTK -aec ORTHANC orthanc 4242 \
    -k "QueryRetrieveLevel=STUDY" \
    -k "StudyInstanceUID=" 2>/dev/null \
  | grep -c "StudyInstanceUID"
'
```

#### Manual C-MOVE (single study)

```bash
kubectl exec -n flip-trust "$dcmtk_pod" -- \
  movescu -aet DCMTK -aec ORTHANC -aem XNAT \
    orthanc 4242 \
    -k "QueryRetrieveLevel=STUDY" \
    -k "StudyInstanceUID=1.2.826.0.1.3680043.8.498.382381661119149256950192"
```

The study data is sent to XNAT's prearchive.

#### Bulk Import via Script

A direct import script is available:
```bash
kubectl cp deploy/providers/kubernetes/scripts/imaging-import-worker.py \
  "$dcmtk_pod":/tmp/imaging-import-worker.py
kubectl exec -n flip-trust "$dcmtk_pod" -- pip install pynetdicom psycopg2-binary -q
kubectl exec -n flip-trust "$dcmtk_pod" -- \
  BATCH_SIZE=150 python3 /tmp/imaging-import-worker.py
```

### 2.7 Checking DICOM Connectivity

#### DICOM Port Map

| Service | AE Title | Host | Port | Purpose |
|---------|---------|------|------|---------|
| XNAT SCP | `XNAT` | xnat-web | 8104 | Receives C-STORE from PACS |
| Orthanc | `ORTHANC` | orthanc | 4242 | PACS — stores DICOM studies |
| Imaging Worker | `FLIPIMPORT` | (any) | — | C-MOVE source AE |

#### XNAT SCP Receiver Configuration

Verify the SCP receiver is configured in the XNAT DB:

```bash
kubectl exec -n flip-trust trust-release-flip-trust-xnat-db-0 -- psql -U xnat -d xnat -c \
  "SELECT id, ae_title, port, direct_archive, custom_processing, identifier FROM xhbm_dicomscpinstance;"
```

Expected output:
`ae_title=XNAT, port=8104, direct_archive=t, custom_processing=t, identifier=dqrObjectIdentifier`

If missing or wrong, recreate it via the REST API (see §2.2 — prefer the API
over direct DB inserts: XNAT binds the SCP listener and caches receiver config
at the service layer, so DB-only changes need a restart to take effect).

---

## 3. OMOP Data Issues

### Check OMOP DB Status

```bash
# Test connection
kubectl exec -n flip-trust deploy/trust-release-flip-trust-data-access-api -- \
  curl -s http://localhost:8000/health

# Check OMOP data counts
kubectl exec -n flip-trust trust-release-flip-trust-omop-db-0 -- \
  psql -U omop -d omop -c \
  "SELECT 'person' AS tbl, COUNT(*) FROM omop.person
   UNION ALL
   SELECT 'image_occurrence', COUNT(*) FROM omop.image_occurrence
   UNION ALL
   SELECT 'image_feature', COUNT(*) FROM omop.image_feature;"
```

Expected: **4,186 persons**, **4,186 image_occurrences**, **5,939 image_features**

### Cohort Queries Fail with `query_failed` (restored backup)

**Symptom:** Every cohort query returns 500 `{"detail": "query_failed"}` from
data-access-api; its logs show
`FATAL: password authentication failed for user "data_analyst_reader"`.
Unqualified table names (`SELECT * FROM person`) may also 400 with
`The table 'person' does not exist.`

**Root Cause:** The OMOP data directory was restored from a backup that
**already contains** the `data_analyst_reader` role (with the original
password) and lacks the database-level `search_path`. The omop-db postStart
hook's old `IF NOT EXISTS` guard skipped both, so the role's password never
matched `DATA_ACCESS_POSTGRES_PASSWORD` from the chart Secret.

**Fix:** The chart's `omop-db.yaml` postStart hook now `ALTER ROLE`s the
password unconditionally and sets the `search_path` on every container start.
On a live cluster (without restarting omop-db):

```bash
kubectl exec -n flip-trust trust-release-flip-trust-omop-db-0 -- \
  psql -U postgres -d trustomopdb -c \
  "ALTER ROLE data_analyst_reader WITH PASSWORD '<data-access-postgres-password from Secret>';
   ALTER DATABASE trustomopdb SET search_path = omop, public;"
# data-access-api pools connections — restart to pick up the new search_path:
kubectl rollout restart deployment/trust-release-flip-trust-data-access-api -n flip-trust
```

### Rebuilding OMOP Data

If the OMOP data is missing (e.g., new deployment), the init job handles
restoration automatically. Manual trigger:

```bash
kubectl delete job -n flip-trust trust-release-flip-trust-omop-db-init
helm upgrade trust-release deploy/providers/kubernetes -n flip-trust \
  -f deploy/providers/kubernetes/values.yaml \
  --set imageTag=stag \
  --set omopDb.initJob.run=true
```

If S3 auth fails (wrong AWS profile):

```bash
# Check what profile the omop-db pod is using
kubectl exec -n flip-trust trust-release-flip-trust-omop-db-0 -- \
  bash -c 'aws sts get-caller-identity --profile flipstag 2>/dev/null || echo "No valid AWS session"

# The init job mounts ~/.aws from the host and uses AWS_PROFILE=flipstag
# Ensure your k3s host has a valid session: aws sso login --profile flipstag
```

---

## 4. Trust Registration and Heartbeat

### Check Trust Status

```bash
# Check trust-api heartbeat
TRUST_API_POD=$(kubectl get pods -n flip-trust -l app.kubernetes.io/component=trust-api -o jsonpath='{.items[0].metadata.name}')
kubectl logs -n flip-trust "$TRUST_API_POD" --tail=20

# Check trust registration on hub
# On the Central Hub (staging), the trust should appear as connected
```

### Register a New Trust

Registration is done **on the hub**, by the same CODE-named kit flow every
trust uses — not by a K8s-specific script. From the repo root:

```bash
make new-trust TRUST_CODE=Trust_MyNew TRUST_NAME="My New Trust"
make -C deploy/providers/AWS register-trusts KIT=Trust_MyNew PROD=stag  # mints creds + FL slot
make sync-trust-kit KIT=Trust_MyNew PROD=stag                           # fills Hub-shared block
```

Then translate the kit into the cluster and deploy:

```bash
make -C deploy/providers/kubernetes sync-kit KIT=Trust_MyNew PROD=stag  # patches Secret + writes override
make -C deploy/providers/kubernetes up OVERRIDES_FILE=k8s-trust-Trust_MyNew.yaml
```

`sync-kit` patches the per-trust keys into the Kubernetes Secret and writes the
secret-free `k8s-trust-Trust_MyNew.yaml` override (hub URL, FL backend, AWS
region, fl-client bucket, slot-aware kit path). See the chart README Quickstart.

### Heartbeat Failure

If trust-api logs show no heartbeats or `401`:

1. **`401 "API key is missing"`** → the API-key **header** is mismatched. The
   hub reads the key from `TRUST_API_KEY_HEADER` (platform default
   `Authorization`); the chart default matches it. A "missing" (not "invalid")
   401 is the header *name*, not the key value. Re-run `sync-kit` or check the
   `TRUST_API_KEY_HEADER` in your override / ConfigMap.
2. **`401 invalid`** → the key value is wrong. Confirm the Secret holds the
   registered key: re-run `make -C deploy/providers/kubernetes sync-kit KIT=<CODE>`.
   The hub stores only the SHA-256 hash, so re-registration is idempotent.
3. Verify `CENTRAL_HUB_API_URL` is correct and reachable.
4. Check NetworkPolicies aren't blocking egress.

```bash
# Test outbound connectivity
TRUST_API_POD=$(kubectl get pods -n flip-trust -l app.kubernetes.io/component=trust-api -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n flip-trust "$TRUST_API_POD" -- \
  curl -s -o /dev/null -w "%{http_code}" https://stag.flip.aicentre.co.uk/api/health
```

---

## 5. XNAT HTTPS Issues

### nginx HTTPS Termination

The XNAT nginx proxy terminates HTTPS. If you see SSL errors:

```bash
# Check nginx config
NGINX_POD=$(kubectl get pods -n flip-trust -l app.kubernetes.io/component=xnat-nginx -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n flip-trust "$NGINX_POD" -- cat /etc/nginx/conf.d/default.conf

# Check nginx logs
kubectl logs -n flip-trust "$NGINX_POD"
```

### Imaging API Can't Reach XNAT via HTTPS

The imaging-api uses `http://xnat-web:8080` internally (over the cluster network).
The `XNAT_URL` env var for imaging-api should **not** use HTTPS:

```yaml
# Correct — use HTTP over cluster DNS
XNAT_URL: http://xnat-web:8080
```

The nginx proxy (port 443) is for external access only.

---

## 6. Debug Scripts

A collection of diagnostic scripts is available in `/tmp/` on the development
machine and in `deploy/providers/kubernetes/scripts/`. Key scripts:

| Script | Purpose |
|--------|---------|
| `imaging-import-worker.py` | DQR bypass — direct C-MOVE import worker |
| `sync_k8s_kit.py` | Sync a registered trust kit into the cluster (Secret + override) |

### Available Tools on DCMTK Pod

| Tool | Command | Purpose |
|------|---------|---------|
| DCMTK | `echoscu`, `findscu`, `movescu`, `storescu` | DICOM networking |
| Python | `python3` with `pydicom`, `pynetdicom` | DICOM processing |
| pip | `pip install` | Install additional tools |

---

## 7. FL Client / FL Server Connection Issues

The fl-client runs in the K8s cluster and connects to the hub-side fl-server
via gRPC over mTLS (port 8002) through an NLB (`fl.stag.flip.aicentre.co.uk`).

### 7.1 Network Policy Blocking Egress to Port 8002

**Symptom:** `Connection refused [Errno 111]` when the fl-client pod tries to
connect to `fl-server-net-1:8002` or `fl.stag.flip.aicentre.co.uk:8002`.
Other pods (e.g., `python:3.12-slim` test pod) can connect successfully.

**Root Cause:** The `trust-release-flip-trust-egress` NetworkPolicy only allows
egress on ports 53 (UDP/TCP), 80 (TCP), and 443 (TCP). Port 8002 is blocked.

**Check:**
```bash
kubectl get networkpolicy -n flip-trust trust-release-flip-trust-egress -o yaml
```

Look for port 8002 in `spec.egress[0].ports`.

**Fix (ad-hoc):**
```bash
kubectl patch networkpolicy -n flip-trust trust-release-flip-trust-egress --type='json' -p='[
  {"op": "add", "path": "/spec/egress/0/ports/-", "value": {"port": 8002, "protocol": "TCP"}}
]'
```

**Fix (permanent — Helm chart):** Add 8002 to `networkPolicies.egress.ports` in
the values file:
```yaml
networkPolicies:
  egress:
    ports:
      - port: 53
        protocol: UDP
      - port: 53
        protocol: TCP
      - port: 80
        protocol: TCP
      - port: 443
        protocol: TCP
      - port: 8002
        protocol: TCP
```

### 7.2 Certificate Key Usage BIT_INCORRECT

**Symptom:** fl-client pod logs show:
```
Handshake failed with error SSL_ERROR_SSL: error:1000012e:SSL
routines:OPENSSL_internal:KEY_USAGE_BIT_INCORRECT: Invalid certificate
verification context
```

gRPC channel is created but immediately closes with `Not Connected`.

The TCP connection succeeds (NLB passes traffic through) but the TLS handshake
fails on the client side due to the server certificate.

**Root Cause:** The server certificate was re-generated with X509v3 Key Usage
extensions (`keyEncipherment, dataEncipherment`) and Extended Key Usage
(`TLS Web Server Authentication`). The original NVFLARE certificates have
*no* Key Usage or Extended Key Usage extensions. OpenSSL's gRPC implementation
rejects certificates with these restrictions during mTLS negotiation.

**Check:**
```bash
openssl x509 -in /path/to/server.crt -noout -text | grep -A3 "X509v3 Key Usage"
```

If any Key Usage or Extended Key Usage lines appear, the cert needs regeneration.

**Fix:** Regenerate the server certificate *without* key usage / extended key
usage extensions. Only include `basicConstraints=CA:FALSE` and the
`subjectAltName`:

```bash
cat > server_ext.cnf << 'EOF'
[v3_req]
basicConstraints = CA:FALSE
subjectAltName = @alt_names

[alt_names]
DNS.1 = fl-server-net-1
DNS.2 = fl.stag.flip.aicentre.co.uk
EOF

openssl req -new -key server.key -out server.csr -subj "/CN=fl-server-net-1/O=AICentre"
openssl x509 -req -in server.csr -CA rootCA.pem -CAkey rootCA.key \
  -CAcreateserial -out server.crt -days 3650 -sha256 \
  -extfile server_ext.cnf -extensions v3_req
```

Verify no key usage lines in the output:
```bash
openssl x509 -in server.crt -noout -text | grep -E "Key Usage|Extended Key Usage"
# Should produce no output
```

### 7.3 Missing SAN in Server Certificate

**Symptom:** mTLS handshake fails. Server logs show no connection attempt
events. Client logs may show generic SSL errors.

**Root Cause:** The server certificate's Subject Alternative Name (SAN) only
contained `DNS:fl-server-net-1` (the Docker service name). The fl-client
connects via the NLB DNS name `fl.stag.flip.aicentre.co.uk`, which doesn't
match the cert SAN. OpenSSL's certificate verification rejects the mismatch.

**Check:**
```bash
openssl x509 -in server.crt -noout -ext subjectAltName
```

Expected both names to be present:
```
DNS:fl-server-net-1, DNS:fl.stag.flip.aicentre.co.uk
```

**Fix:** Add both SANs when regenerating the certificate (see §7.2 config above).

The `hostAliases` in the fl-client deployment map `fl-server-net-1` to the
NLB IPs, so clients resolve the hostname correctly but the server must present
a cert that matches the hostname the client uses to connect.

### 7.4 Signature PSS Padding Mismatch

**Symptom:** fl-client connects to fl-server but the server logs show
`Authenticator - re-challenge` in a loop without ever progressing to
`verified server identity`. The client may show `Not Connected` after the
handshake.

**Root Cause:** The `signature.json` file in the NVFLARE participant kit
contains signatures for all startup files (e.g., `server.crt`, `start.sh`).
These are verified during the mTLS handshake. NVFLARE signs them using **PSS
padding** with SHA-256 (`padding.PSS(mgf=MGF1(SHA256()), salt_length=MAX)`).
If the signatures were re-generated using **PKCS1v15 padding** (the default),
the verification fails silently and the connection drops.

**Check:** The `nvflare.fuel.sec.security_content_service.SecurityContentService`
uses `_content_padding()` which calls `padding.PSS(...)`. Tracing shows:
```
_verify_content() -> _content_padding() -> PSS(mgf=MGF1(SHA256()), salt_length=MAX)
```

**Fix:** Re-sign `signature.json` using PSS padding:
```python
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes

signature = base64.b64encode(
    private_key.sign(
        data=file_content,
        padding=padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH
        ),
        algorithm=hashes.SHA256(),
    )
).decode()
```

A Python helper script is available at:
`deploy/providers/kubernetes/scripts/resign_signatures.py`

### 7.5 EFS File Permission Lost (sub_start.sh)

**Symptom:** fl-server container exits immediately (`START_STOP` status), even
though the task starts and the NLB target registers as healthy momentarily.

**Root Cause:** During EFS sync (S3 → EFS), the `sub_start.sh` script loses
its execute permission (`chmod +x`). The entrypoint tries to `./sub_start.sh &
&& wait` but exits because the file is not executable.

**Check:**
```bash
aws s3 sync s3://.../cloud/@mnt/startup/ /tmp/check_efs/ 2>/dev/null
ls -la /tmp/check_efs/sub_start.sh  # Should be -rwxr-xr-x, not -rw-r--r--
```

**Fix:**
```yaml
# Add postSync command in EFS sync task:
command: [
  "sh", "-c",
  "aws s3 sync s3://.../startup/ /mnt/startup/ --delete --no-progress &&
   chmod +x /mnt/startup/*.sh"
]
```

On an already-synced EFS:
```bash
efs_mount_point=$(df | grep :/ | awk '{print $6}')
chmod +x "$efs_mount_point"/startup/*.sh
```

### 7.6 Entrypoint Background-Process Exit (start.sh)

**Symptom:** fl-server task starts, NLB target registers as healthy, then the
container exits with code 0 almost immediately. The container logs show the
server starting, then silence.

**Root Cause:** The NVFLARE container's `start.sh` script backgrounds
`sub_start.sh &` and then runs `wait`. When `start.sh` is *executed* (via
`./start.sh`) instead of *sourced* (via `. ./start.sh` or `source start.sh`),
the background job (`sub_start.sh`) becomes orphaned in a subshell. The
`wait` in the parent shell sees no background jobs and returns immediately,
causing the container to exit.

**Fix (fl-server ECS task definition):** Override the entrypoint/command to
source the script instead:
```json
"command": ["sh", "-c", ". ./start.sh && wait"]
```

**Fix (fl-client K8s deployment):** In the Helm chart's `fl-client.yaml`:
```yaml
command: ["/bin/sh", "-c", ". /opt/nvflare/startup/start.sh && wait"]
```

### 7.7 AWS Credentials Expired in K8s Secret

**Symptom:** fl-client pod logs show `AccessDenied` when trying to download
participant kit from S3. Trust's omop-db-init job also fails with S3 errors.

**Root Cause:** The K8s Secret `aws-credentials` (mounted by init containers)
contains stale AWS SSO credentials. SSO sessions expire after 12-24 hours.

**Check:**
```bash
kubectl exec -n flip-trust <pod> -- aws sts get-caller-identity \
  --profile flipstag 2>/dev/null || echo "Credentials expired"
```

**Fix:** Refresh credentials on the host and re-create the Secret:
```bash
aws sso login --profile flipstag

# Re-create K8s secret with fresh credentials
kubectl create secret generic aws-credentials \
  -n flip-trust \
  --from-file=$HOME/.aws/credentials \
  --from-file=$HOME/.aws/config \
  --dry-run=client -o yaml | kubectl apply -f -
```

Pod init containers must be restarted after secret update (delete pods to
force re-init).

### 7.8 EFS Sync Permission Issues (Root vs UID 1001)

**Symptom:** Certs are present on the EFS when written via the root access
point (`fsap-...`, UID 0), but the fl-server container (running as UID 1001)
cannot read them, or the running server picks up stale old certs.

**Root Cause:** EFS has multiple access points for the same filesystem path.
The root AP (`fsap-02c45969fbdf3d9c1`, UID 0) and the fl-server AP
(`fsap-0ffebc76dcf840674`, UID 1001) both point to the same
`/fl-server-net-1/startup/` path, but files written via one AP are visible
from the other. However, if the fl-server caches the directory listing at
startup, a new EFS sync while the server is running won't take effect until
the server is restarted.

**Check:**
```bash
# Verify cert content through fl-server's access point
TASK_ARN=$(aws ecs run-task --task-definition efs-sync:1 ...)
aws ecs execute-command --task $TASK --container app \
  --command "md5sum /opt/nvflare/startup/rootCA.pem"
```

**Fix:** Always force a new deployment after EFS cert sync:
```bash
aws ecs update-service --cluster flip-cluster --service fl-server-net-1 \
  --force-new-deployment
```

### 7.9 Container Image Pull from Private ECR

**Symptom:** Pod in `ErrImagePull` / `ImagePullBackOff` status. Fl-server ECS
task fails with `CannotPullContainerError`.

**Root Cause:** The NVFLARE container images are hosted in a private ECR
repository (account `080369786334` in `eu-west-2`). The K8s cluster or ECS
task execution role lacks permissions to pull from this repository.

**Fix (K8s):** Create an `imagePullSecret` with ECR credentials:
```bash
# Generate ECR auth token
ecr_password=$(aws ecr get-login-password --profile flipstag --region eu-west-2)
kubectl create secret docker-registry ecr-cred \
  --docker-server=080369786334.dkr.ecr.eu-west-2.amazonaws.com \
  --docker-username=AWS \
  --docker-password="$ecr_password" \
  -n flip-trust
```

Then reference it in the Helm values:
```yaml
flClient:
  imagePullSecrets:
    - name: ecr-cred
```

**Fix (ECS):** Ensure the ECS task execution role has:
```json
{
  "Effect": "Allow",
  "Action": [
    "ecr:GetDownloadUrlForLayer",
    "ecr:BatchGetImage",
    "ecr:BatchCheckLayerAvailability"
  ],
  "Resource": "arn:aws:ecr:eu-west-2:080369786334:repository/*"
}
```

### 7.10 gRPC Async Connect Fails on Kernel 7 (Ubuntu 26.04)

**Symptom:** fl-client pod crash-loops with no NVFlare registration output.
The pod logs end at `STARTING CLIENT...` with no further output. After
~60 seconds the faulthandler shows the main thread stuck at
`while not sp_established: sleep(1.0)` and the overseer thread is blocked.
The fl-server never sees the client register.

**Root Cause:** The gRPC C-core's async TCP connect mechanism fails on
kernel 7.0.0 (Ubuntu 26.04). Python's synchronous `socket.connect()` +
`select.poll()` work perfectly, but the C-core's internal pollset / event-engine
cannot complete the three-way handshake asynchronously. Even connecting to
`127.0.0.1` fails.

Versions tested:

| grpcio | Behaviour |
|--------|-----------|
| 1.76.0 (container default) | Async connect stuck in CONNECTING or TRANSIENT_FAILURE |
| 1.78.0 wheel | Segfault (core dump) on import |
| 1.80.0 wheel | Segfault (core dump) on import |

**Fix:** Force NVFlare to use the **asyncio-based gRPC driver**
(`aio_grpc_driver`) instead of the default synchronous one. The aio driver
uses `grpc.aio.secure_channel` / `grpc.aio.insecure_channel`, which rely on
Python's asyncio event loop for async connect — this works on kernel 7.0.

The fix requires two changes:

1. **Change the transport scheme** from `grpc` to `agrpc` in the participant
   kit's `fed_client.json`. `agrpc` is a drop-in replacement — it speaks the
   same gRPC wire protocol over HTTP/2, so the fl-server does not need any
   changes.

2. **Do NOT change `sp_end_point`** in the `overseer_agent` section. The
   `FederatedClientBase.set_sp()` method only creates the cellnet connector
   (which triggers the gRPC connection) when `server != location`. If the SP
   endpoint is rewritten to match the server target, the cell is never
   created and the client never registers.

**Required kit files:** The following files must be present in the startup
kit (e.g., `/opt/flip/k8s-fl-client-kits/Trust_K8s/startup/`):

| File | Purpose |
|------|---------|
| `run_client.py` | Wrapper that modifies `fed_client.json` scheme to `agrpc`, then `exec`s `client_train.py`. Preserves the backup for restore on exit. |
| `patch.py` | Patches `grpc.ssl_channel_credentials` to inject correct client certs and bypasses NVFlare's `_check_secure_content` signature validation (needed because `fed_client.json` was manually edited). |
| `sitecustomize.py` | Auto-loaded at Python startup. Applies the same patches for worker subprocesses. Also sets `GRPC_POLL_STRATEGY=epoll1`. |

**Successful registration log:**
```
2026-05-20 09:53:00,157 - Authenticator - INFO - verified server identity 'fl.stag.flip.aicentre.co.uk'
2026-05-20 09:53:00,204 - Authenticator - INFO - Verified received token and signature successfully
2026-05-20 09:53:00,204 - FederatedClient - INFO - Successfully registered client:Trust_K8s for project net-1.
```

**What was tried (and failed):**

| Approach | Result |
|----------|--------|
| grpcio 1.78.0 / 1.80.0 wheel replacement | Segfault on kernel 7.0 |
| TCP proxy (blocking TLS to NLB, gRPC to localhost) | Proxy works but gRPC can't async-connect even to `127.0.0.1` |
| Unix Domain Sockets (`unix:/tmp/sock`) | Same async connect failure |
| `GRPC_POLL_STRATEGY` (`epoll1`, `poll`, `none`, `poll_cv2`) | None work |
| `GRPC_DNS_RESOLVER=native` | No improvement |
| `channel.channel_ready()` explicit wait | Works locally, but **not needed** — the `agrpc` scheme alone suffices |

---

## Quick Diagnostic Checklist

When something is broken, run these in order:

```bash
# 1. Pod status
kubectl get pods -n flip-trust

# 2. OMOP data
kubectl exec -n flip-trust trust-release-flip-trust-omop-db-0 -- \
  psql -U omop -d omop -c "SELECT COUNT(*) FROM omop.image_occurrence;"

# 3. DICOM connectivity
DCMTK_POD=$(kubectl get pods -n flip-trust -l run=dcmtk -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n flip-trust "$DCMTK_POD" -- echoscu orthanc 4242
kubectl exec -n flip-trust "$DCMTK_POD" -- echoscu xnat-web 8104

# 4. XNAT queue
kubectl exec -n flip-trust trust-release-flip-trust-xnat-db-0 -- \
  psql -U xnat -d xnat -c \
  "SELECT status, COUNT(*) FROM xhbm_queued_pacs_request GROUP BY status;"

# 5. XNAT DQR logs
XNAT_POD=$(kubectl get pods -n flip-trust -l app.kubernetes.io/component=xnat-web -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n flip-trust "$XNAT_POD" -- tail -10 /data/xnat/home/logs/dqr.log

# 6. Helm status
helm list -n flip-trust

# 7. If DQR is failing: check destination AE vs SCP receiver first (§2.1),
#    then receiver identifier (§2.2). The import worker is a last-resort
#    fallback only (no relabel map):
kubectl exec -n flip-trust trust-release-flip-trust-xnat-db-0 -- \
  psql -U xnat -d xnat -c "SELECT DISTINCT destination_ae_title FROM xhbm_queued_pacs_request;"

# 8. FL client connectivity (if training isn't starting)
FL_POD=$(kubectl get pods -n flip-trust -l app.kubernetes.io/component=fl-client -o jsonpath='{.items[0].metadata.name}')
kubectl logs -n flip-trust "$FL_POD" --tail=15 | grep -E "Connected|challenge|Not.*Connect|KEY_USAGE|registered client|Got engine"

# 9. Network policy check (if fl-client gets Connection Refused)
kubectl get networkpolicy -n flip-trust trust-release-flip-trust-egress -o yaml | grep -q 8002 && \
  echo "Port 8002 allowed" || echo "MISSING: Port 8002 in egress policy"
```
