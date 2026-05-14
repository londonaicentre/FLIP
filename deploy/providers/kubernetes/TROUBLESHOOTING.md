# Troubleshooting Guide — FLIP K8s Trust

This guide covers common issues and resolutions for the Kubernetes-deployed trust
services, with a focus on the XNAT DICOM import pipeline.

---

## Table of Contents

1. [Pods Not Starting](#1-pods-not-starting)
2. [XNAT DICOM Import Pipeline Issues](#2-xnat-dicom-import-pipeline-issues)
   - [2.1 The DQR Plugin Fails with PacsNotStorableException](#21-the-dqr-plugin-fails-with-pacsnotstorableexception)
   - [2.2 Running the Imaging Import Worker Manually](#22-running-the-imaging-import-worker-manually)
   - [2.3 C-MOVE Testing from the DCMTK Pod](#23-c-move-testing-from-the-dcmtk-pod)
   - [2.4 Checking DICOM Connectivity](#24-checking-dicom-connectivity)
3. [OMOP Data Issues](#3-omop-data-issues)
4. [Trust Registration and Heartbeat](#4-trust-registration-and-heartbeat)
5. [XNAT HTTPS Issues](#5-xnat-https-issues)
6. [Debug Scripts](#6-debug-scripts)

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

### 2.1 The DQR Plugin Fails with PacsNotStorableException

**Symptom:** The XNAT DQR plugin logs `PacsNotStorableException: null` at
`BasicDicomQueryRetrieveService.java:320`. This happens even though the PACS
(Orthanc) is correctly configured with `storable=true` in the XNAT database.

**Check DQR logs:**
```bash
XNAT_POD=$(kubectl get pods -n flip-trust -l app.kubernetes.io/component=xnat-web -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n flip-trust "$XNAT_POD" -- tail -30 /data/xnat/home/logs/dqr.log
```

**Diagnosis:**

1. **Check PACS is registered correctly in XNAT DB:**
   ```bash
   kubectl exec -n flip-trust trust-release-flip-trust-xnat-db-0 -- psql -U xnat -d xnat -c \
     "SELECT id, ae_title, host, storable, enabled FROM xhbm_pacs;"
   ```
   Expected: `id=1, ae_title=ORTHANC, host=orthanc, storable=t, enabled=t`

2. **Verify DICOM connectivity from the DCMTK pod:**
   ```bash
   dcmtk_pod=$(kubectl get pods -n flip-trust -l run=dcmtk -o jsonpath='{.items[0].metadata.name}')
   kubectl exec -n flip-trust "$dcmtk_pod" -- echoscu orthanc 4242
   ```
   This should return a success message confirming association.

3. **Check the queue status:**
   ```bash
   kubectl exec -n flip-trust trust-release-flip-trust-xnat-db-0 -- psql -U xnat -d xnat -c \
     "SELECT status, COUNT(*) FROM xhbm_queued_pacs_request GROUP BY status;"
   ```

**Root Cause:** The DQR plugin version 2.3.x (both 2.3.1 and 2.3.2) has a bug
that throws `PacsNotStorableException` before initiating any DICOM connection.
Orthanc logs show zero incoming DICOM from XNAT during DQR retries.

**Workaround:** Use the **Imaging Import Worker** (see §2.2) which bypasses the
DQR plugin entirely by performing direct C-MOVE operations using `pynetdicom`.

### 2.2 Running the Imaging Import Worker

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

### 2.3 C-MOVE Testing from the DCMTK Pod

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

### 2.4 Checking DICOM Connectivity

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

Expected output: `ae_title=XNAT, port=8104, direct_archive=t, custom_processing=t`

If missing, reconfigure:
```bash
kubectl exec -n flip-trust trust-release-flip-trust-xnat-db-0 -- psql -U xnat -d xnat -c \
  "DELETE FROM xhbm_dicomscpinstance WHERE ae_title='XNAT';"
kubectl exec -n flip-trust trust-release-flip-trust-xnat-db-0 -- psql -U xnat -d xnat -c \
  "INSERT INTO xhbm_dicomscpinstance (created, disabled, enabled, timestamp, \
   ae_title, anonymization_enabled, custom_processing, direct_archive, identifier, port, \
   routing_expressions_enabled, whitelist_enabled) \
   VALUES (NOW(), 'infinity', true, NOW(), 'XNAT', false, true, true, \
   'dqrObjectIdentifier', 8104, false, false);"
```

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

```bash
cd deploy/providers/kubernetes
python3 register_k8s_trust.py --trust-name Trust_MyNew
```

This generates:
- API keys for hub-to-trust auth
- A Helm values file `k8s-trust-Trust_MyNew.yaml`
- Terraform instructions to register the trust in the hub

### Heartbeat Failure

If trust-api logs show no heartbeats or `401 Unauthorized`:

1. Check `TRUST_API_KEY` matches between trust and hub
2. Verify `CENTRAL_HUB_API_URL` is correct and reachable
3. Check NetworkPolicies aren't blocking egress

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
| `register_k8s_trust.py` | Generate trust registration assets |

### Available Tools on DCMTK Pod

| Tool | Command | Purpose |
|------|---------|---------|
| DCMTK | `echoscu`, `findscu`, `movescu`, `storescu` | DICOM networking |
| Python | `python3` with `pydicom`, `pynetdicom` | DICOM processing |
| pip | `pip install` | Install additional tools |

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

# 7. Run import worker (if DQR is failing)
python3 deploy/providers/kubernetes/scripts/imaging-import-worker.py
```
