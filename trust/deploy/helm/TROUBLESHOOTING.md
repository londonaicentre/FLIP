# Troubleshooting Guide — FLIP K8s Trust

Operator troubleshooting for the Kubernetes trust now lives in the docs:
**[Trust node on Kubernetes](https://londonaicentreflip.readthedocs.io/en/latest/troubleshooting/trust-node-kubernetes.html)**, part of the
[Troubleshooting](https://londonaicentreflip.readthedocs.io/en/latest/troubleshooting.html) section. It covers pods
that do not start, deploy conflicts and timeouts, the XNAT DICOM import pipeline, dcm2niix and the NIfTI backlog,
OMOP data and seeding, heartbeats, FL client egress and the governance document.

What stays here is maintainer material: how `smoke-cstore` reaches its verdict, the debug scripts, historical
FL connection notes and fixed regressions. Headings that code, tests and other files cite by number or anchor are
kept as short pointers to their new home.

---

## 2. XNAT DICOM Import Pipeline Issues

### 2.1 PacsNotStorableException — destination AE/port mismatch

Moved to the docs: [PacsNotStorableException: null in dqr.log](https://londonaicentreflip.readthedocs.io/en/latest/troubleshooting/trust-node-kubernetes.html#ts-k8s-pacs-not-storable).

### 2.2 Studies Received but Land in the Unassigned Prearchive

Moved to the docs: [Studies arrive but land in the Unassigned prearchive](https://londonaicentreflip.readthedocs.io/en/latest/troubleshooting/trust-node-kubernetes.html#ts-k8s-unassigned).

### 2.3 imaging-api Gets 401 from XNAT (flipServiceAccount)

Moved to the docs: [imaging-api gets 401 from XNAT](https://londonaicentreflip.readthedocs.io/en/latest/troubleshooting/trust-node-kubernetes.html#ts-k8s-xnat-401).

### 2.3a dcm2niix Never Registered (admin missing ContainerManager)

Moved to the docs: [No NIfTI is produced: the dcm2niix command was never registered](https://londonaicentreflip.readthedocs.io/en/latest/troubleshooting/trust-node-kubernetes.html#ts-k8s-dcm2niix-not-registered).

### 2.3b dcm2niix Runs but Produces No NIfTI (Container Service PVC mounts)

Moved to the docs: [dcm2niix runs but fails: Unable to find any DICOM images in /input](https://londonaicentreflip.readthedocs.io/en/latest/troubleshooting/trust-node-kubernetes.html#ts-k8s-dcm2niix-no-nifti).

### 2.3c dcm2niix Registered Against a Stale Image

Moved to the docs: [XNAT has 0 'dcm2niix-scan' wrappers on the pinned ...](https://londonaicentreflip.readthedocs.io/en/latest/troubleshooting/trust-node-kubernetes.html#ts-k8s-dcm2niix-stale-image).

### 2.3d dcm2niix Jobs Swamp the Node, or Wait for a Slot

Moved to the docs: [dcm2niix jobs swamp the node, or wait for a slot](https://londonaicentreflip.readthedocs.io/en/latest/troubleshooting/trust-node-kubernetes.html#ts-k8s-dcm2niix-jobs-cap).

### 2.3e Scans Archived Without NIfTI (Converting the Backlog)

Moved to the docs: [Some archived scans have no NIfTI](https://londonaicentreflip.readthedocs.io/en/latest/troubleshooting/trust-node-kubernetes.html#ts-k8s-nifti-backlog).

### 2.4 Forcing a Re-pull (status stuck on "Processing")

Moved to the docs: [Status stays on "Processing": forcing a re-pull](https://londonaicentreflip.readthedocs.io/en/latest/troubleshooting/trust-node-kubernetes.html#ts-k8s-force-repull).

### 2.6a Exposing the DICOM Receiver to an External PACS (`dicomService`)

Moved to the docs: the values and network rules are in
[PACS — exposing the DICOM receiver](https://londonaicentreflip.readthedocs.io/en/latest/components/component-pacs.html),
and the render failures and the clean-up of a hand-made Service in
[The chart refuses to render with a real PACS](https://londonaicentreflip.readthedocs.io/en/latest/troubleshooting/trust-node-kubernetes.html#ts-k8s-pacs-render).

### 2.7 C-ECHO Passes, C-STORE Aborts (`AbstractMethodError` in dicom.log)

The symptom, the three reasons an `xnat-web` pod keeps an old plugin roster, and the fix are in the docs:
[C-ECHO passes, C-STORE aborts](https://londonaicentreflip.readthedocs.io/en/latest/troubleshooting/trust-node-kubernetes.html#ts-k8s-cstore-aborts). The deploy timeout behind reason 3 is
[Deploy fails with resource Job/…/xnat-init not ready](https://londonaicentreflip.readthedocs.io/en/latest/troubleshooting/trust-node-kubernetes.html#ts-k8s-xnat-init-timeout).

**What `smoke-cstore` judges.** Success is Orthanc reporting `0` failed instances **and**
receiver-side evidence that the object landed, polled for up to `PREARCHIVE_TIMEOUT` seconds,
strongest first:

1. a new DICOM object in XNAT's prearchive (`/data/xnat/prearchive`) carrying the
   `SOPInstanceUID`, else the `StudyInstanceUID`, that was sent — matched against the
   *receiver's own* clock;
2. a new line in `/data/xnat/home/logs/received.log` since the line-count mark taken before the
   store — that is, *a new receipt during the window, preferring lines from `SENDER_AE`
   (default `ORTHANC`) when they are distinguishable*. It does not identify the object: a DQR /
   C-MOVE retrieval reaches XNAT from the same Orthanc under the same calling AE, so **a
   concurrent import from the same PACS cannot be told apart**. Matching the stored object
   properly — by a value `anon_script.das` leaves intact, or by the hashed SOP UID in the stored
   file name — is a follow-up. The importer-crash signatures below are unaffected: they fail the
   run whichever import hits them;
3. a new prearchive object by arrival time alone — a genuine last resort, reported as such,
   since a concurrent DQR/C-MOVE import looks identical.

**Exit codes: 0 pass, 1 failure, 2 inconclusive.** (3) on its own is only honest evidence where
`received.log` does not exist on the receiver; there it stays a pass (exit 0) with its warning.
Where `received.log` *does* exist and gained no line for this store, the receiver's own success
logger stayed silent through a transfer it records every receipt of — so arrival time alone is
neither a pass nor a demonstrated failure, and the run exits **2**. The script probes that
existence explicitly when it takes the pre-store mark: `wc -l` reports `0` for a missing file and
an empty one alike, which cannot tell the two apart. `make -C trust/deploy/helm smoke-cstore`
maps 2 to a loud warning and still exits 0, so an inconclusive run does not break a deploy — but
anything reading `$?` from the script directly sees a status that is not "passed".

(2) is the normal path on a FLIP trust. `configure-xnat.sh` sets `anonymizationEnabled: true` on
the SCP receiver and `anon_script.das` rewrites `(0020,000D)`, `(0020,000E)` and `(0008,0018)`
through `hashUID`, so the object XNAT stores does **not** carry the UIDs Orthanc holds and (1)
cannot match. `received.log` is written after that rewrite.

It used to require `dicom.log` to gain lines instead. That is the wrong file twice over: XNAT
records successful receipts in `received.log` and keeps `dicom.log` for *errors*, and this
deployment never writes `dicom.log` at all (0 bytes while every store imports correctly), so a
healthy path failed with "dicom.log gained no lines". Its growth is now reported as context and
can never pass a run on its own. The importer signatures (`AbstractMethodError`,
`NoSuchMethodError`, `unable to read DICOM object null`) still fail the run outright, read from
both `dicom.log` and the `xnat-web` container log — whose window starts at the store (`kubectl
logs --since-time`), not at the moment the log is read, so a crash in the first seconds after
the association is not missed on a run that polls to the deadline.

Knobs: `INSTANCE_ID=<orthanc id>` (send a known instance), `PREARCHIVE_DIR`,
`PREARCHIVE_TIMEOUT` (default 90s), `PREARCHIVE_INTERVAL`, `SETTLE_SECONDS`, `DICOM_LOG`,
`RECEIVED_LOG`, `SENDER_AE`, `KUBE_CONTEXT`. The verdict itself is `scripts/cstore_verdict.py`,
unit-tested in `tests/test_cstore_verdict.py` with no cluster — including the evidence builder,
so the shell script holds no parsing of its own.

A failing run that names *no received.log line and no new prearchive object* means the
association went somewhere that is not this XNAT — check the SCP receiver Orthanc dialled
(§2.1) before suspecting the plugins.

---

## 6. Debug Scripts

A collection of diagnostic scripts is available in `/tmp/` on the development
machine and in `trust/deploy/helm/scripts/`. Key scripts:

| Script | Purpose |
|--------|---------|
| `sync_k8s_kit.py` | Sync a registered trust kit into the cluster (Secret + override) |
| `smoke-cstore.sh` | Drive a real C-STORE through the PACS and verify XNAT received it (§2.7) |
| `cstore_verdict.py` | The smoke's verdict logic, unit-tested without a cluster (§2.7) |
| `check_status.py` | Full deployment smoke, including the plugin-roster comparison (§2.7) |

### Available Tools on DCMTK Pod

| Tool | Command | Purpose |
|------|---------|---------|
| DCMTK | `echoscu`, `findscu`, `movescu`, `storescu` | DICOM networking |
| Python | `python3` with `pydicom`, `pynetdicom` | DICOM processing |
| pip | `pip install` | Install additional tools |

---

## 7. FL Client / FL Server Connection Issues

### 7.1 Network Policy Blocking Egress to Port 8002

Moved to the docs: [The FL client gets Connection refused [Errno 111]](https://londonaicentreflip.readthedocs.io/en/latest/troubleshooting/trust-node-kubernetes.html#ts-k8s-fl-port-blocked).

The entries below are historical notes from bringing an FL client up against the AWS FL server by hand (EFS, ECR,
hand-made certificates). Current deployments provision kits with `make -C fl-services/<backend> provision` and stage
them with `make -C trust/deploy/helm stage-kit`; never edit a provisioned kit.

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
(`TLS Web Server Authentication`). The original FLARE certificates have
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

**Root Cause:** The `signature.json` file in the FLARE participant kit
contains signatures for all startup files (e.g., `server.crt`, `start.sh`).
These are verified during the mTLS handshake. FLARE signs them using **PSS
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

There is no bundled helper script for this — re-sign each file listed in
`signature.json` by hand with the snippet above (or your own script built on
it) and rewrite `signature.json` with the resulting base64 signatures.

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

**Root Cause:** The FLARE container's `start.sh` script backgrounds
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
participant kit from S3. The omop-db vocab-load Job (the chart's only AWS-bearing Job) also fails with S3 errors.

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

**Root Cause:** The FLARE container images are hosted in a private ECR
repository (the `<ecr-account-id>` account in `eu-west-2`). The K8s cluster or ECS
task execution role lacks permissions to pull from this repository.

**Fix (K8s):** Create an `imagePullSecret` with ECR credentials:
```bash
# Generate ECR auth token
ecr_password=$(aws ecr get-login-password --profile flipstag --region eu-west-2)
kubectl create secret docker-registry ecr-cred \
  --docker-server=<ecr-account-id>.dkr.ecr.eu-west-2.amazonaws.com \
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
  "Resource": "arn:aws:ecr:eu-west-2:<ecr-account-id>:repository/*"
}
```

### 7.10 gRPC Async Connect Fails on Kernel 7 (Ubuntu 26.04)

**Symptom:** fl-client pod crash-loops with no FLARE registration output.
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

**Fix:** Force FLARE to use the **asyncio-based gRPC driver**
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
| `patch.py` | Patches `grpc.ssl_channel_credentials` to inject correct client certs and bypasses FLARE's `_check_secure_content` signature validation (needed because `fed_client.json` was manually edited). |
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

## 8. Trust Governance Policy (FLIP#1259)

Moved to the docs: [A service will not start after a governance edit](https://londonaicentreflip.readthedocs.io/en/latest/troubleshooting/trust-node-kubernetes.html#ts-k8s-governance) and
[The governance document is right, but the pods behave as if it were absent](https://londonaicentreflip.readthedocs.io/en/latest/troubleshooting/trust-node-kubernetes.html#ts-k8s-governance-ignored).

---

## 10. Preflight Looks for the Wrong Kit File (PROD tokens)

### Symptom

`make -C trust/deploy/helm preflight KIT=<CODE> PROD=lza-stag` fails check 4 with
`Kit file not found: .../trust/.env.<CODE>.development`, while the Makefile's own targets
resolve the same kit correctly.

### Cause

`scripts/preflight.sh` carried its own `PROD` → suffix mapping, written before the LZA env
tokens existed, so `lza` / `lza-stag` fell through to `development`.

### Fix

Fixed in-tree: `deploy/env_mode.mk` is now the only `PROD` → suffix map. The Makefile derives
the token from it and injects it (`KIT_ENV=` into `preflight.sh`, `--env` into `sync_k8s_kit.py`);
neither script keeps a copy, and running one bare with `PROD` set but no token is refused with
a message naming the Makefile target, instead of defaulting to `development`. Expected mapping:

| `PROD` | kit file |
|---|---|
| (unset) | `trust/.env.<CODE>.development` |
| `stag` | `trust/.env.<CODE>.stag` |
| `true` | `trust/.env.<CODE>.production` |
| `lza` | `trust/.env.<CODE>.lza-prod` |
| `lza-stag` | `trust/.env.<CODE>.lza-stag` |
