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

# FLIP Trust — Kubernetes Helm Chart

This Helm chart deploys the FLIP trust-side services on Kubernetes. It follows
the same **zero inbound trust** architecture as the Docker Compose deployment:
trust services only make outbound connections to the Central Hub and the FL
server; no inbound ports are exposed from the K8s cluster.

> **⚠️  Early-access.**
> The three blockers found in the original single-node k3s validation —
> `xnat-nginx` exiting after entrypoint, `xnat-web` crash-looping on a DB
> password mismatch, and the `fl-client` failing to hold its NVFLARE
> connection — were fixed on branch `376_KubernetesHelmChartForTrust-sideDeployment`
> (PR [#420](https://github.com/londonaicentre/FLIP/pull/420)). `Trust_K8s` now
> connects to the stag Central Hub and runs healthy.
>
> Remaining work before this chart is production-ready is tracked in:
>
> - [#593](https://github.com/londonaicentre/FLIP/issues/593) — deployment
>   robustness: automated K8s-trust kit provisioning, egress-config persistence
>   across `make sync-kit`, and fl-api zombie hardening on the hub.
> - [#516](https://github.com/londonaicentre/FLIP/issues/516) — NetworkPolicy
>   egress allowlist audit + threat model.
> - [#530](https://github.com/londonaicentre/FLIP/issues/530) — RBAC and
>   PodSecurity hardening.
>
> The kernel-7 gRPC issue ([#527](https://github.com/londonaicentre/FLIP/issues/527)),
> sidecar FL-client mode ([#528](https://github.com/londonaicentre/FLIP/issues/528)),
> and XNAT/Orthanc Ingress ([#529](https://github.com/londonaicentre/FLIP/issues/529))
> were triaged and closed as out-of-scope / won't-fix.

## Prerequisites

- **Kubernetes cluster** 1.28+ (EKS, AKS, or on-prem)
- **Helm** 3.16+
- **kubectl** configured with cluster access
- **NVIDIA GPU Operator** (if GPU workloads are enabled)
- **External Secrets Operator** or **Secrets Store CSI Driver** (recommended for
  production secrets management)

## Quickstart

A K8s trust is registered with the hub **exactly like any other trust** — by a
CODE-named *kit*. Registration is done once, centrally, by the DB-backed
`register_trust` CLI (it mints the per-trust credentials and claims an FL kit
slot); the chart never registers anything itself. The flow is:

```
 hub side (once)            cluster side (this chart)
 ─────────────────          ─────────────────────────
 new-trust ─► register ─► sync-trust-kit ─► sync-kit ─► up ─► (add-k8s-trust)
            writes trust/.env.<CODE>.<env>   patches Secret + writes override
```

### 1. Register the trust on the hub (produces the kit)

```bash
# From the repo root. <CODE> is this trust's name, e.g. Trust_K8s.
make new-trust TRUST_CODE=<CODE> TRUST_NAME="<Human Name>"
make -C deploy/providers/AWS register-trusts KIT=<CODE> PROD=stag   # mints creds + claims FL slot
make sync-trust-kit KIT=<CODE> PROD=stag                            # fills the Hub-shared block
```

This writes `trust/.env.<CODE>.stag` containing the per-trust keys
(`TRUST_API_KEY`, `TRUST_INTERNAL_SERVICE_KEY`) and the Hub-shared block
(`AES_KEY_BASE64`, `CENTRAL_HUB_API_URL`, FL settings). The hub stores only the
SHA-256 hash of the API key — re-running registration is idempotent.

### 2. Provide the infrastructure secrets

The kit owns only the per-trust keys. The chart's *other* secrets (XNAT, OMOP,
Orthanc, Grafana, S3 kit-sync credentials) are deployment-specific — supply them
via the chart's built-in Secret template (`secrets.create=true` + a
`values-secrets.yaml`, see the [Secrets Reference](#secrets-reference)) or create
the Secret externally. `make sync-kit` (next step) patches the per-trust keys
*on top* of this Secret without touching the infra keys.

### 3. Sync the kit into the cluster

```bash
make -C deploy/providers/kubernetes sync-kit KIT=<CODE> PROD=stag
```

This reads `trust/.env.<CODE>.stag`, patches the per-trust keys
(`trust-api-key`, `trust-internal-service-key[-header]`, `aes-key-base64`) into
the chart's Kubernetes Secret (`trust-release-flip-trust-secrets`), and writes a
secret-free Helm override `k8s-trust-<CODE>.yaml` carrying the hub URL, FL
backend, AWS region, the fl-client kit bucket, and the slot-aware NVFLARE kit
path. Plaintext keys go straight into the Secret over kubectl's TLS channel —
never to disk. (Run it after the namespace/Secret exist; if they don't yet,
deploy once first, then re-run.)

### 4. Install / upgrade the chart

```bash
make -C deploy/providers/kubernetes up OVERRIDES_FILE=k8s-trust-<CODE>.yaml
```

Equivalent raw Helm:

```bash
helm upgrade --install trust-release ./deploy/providers/kubernetes/ \
  --namespace flip-trust --create-namespace \
  -f deploy/providers/kubernetes/values.yaml \
  -f deploy/providers/kubernetes/values-secrets.yaml \
  -f deploy/providers/kubernetes/k8s-trust-<CODE>.yaml
```

### 5. Verify the trust is polling

```bash
kubectl get pods -n flip-trust
kubectl logs -n flip-trust -l app.kubernetes.io/component=trust-api
# Expect: POST .../api/trust/heartbeat "HTTP/1.1 200 OK"
#         GET  .../api/tasks/pending   "HTTP/1.1 200 OK"
```

A `401 "API key is missing"` means the API-key **header** is mismatched — the
chart default `TRUST_API_KEY_HEADER` is `Authorization` (the platform default);
override it only if your hub uses a different header.

### 6. (FL training only) Open the FL-server NLB

Polling needs nothing more. For FL *training*, the K8s node's FL client must
reach the hub's FL server, so open the NLB to the node's public/egress IP:

```bash
make -C deploy/providers/AWS add-k8s-trust K8S_TRUST_IP=<node-public-ip> PROD=stag
```

## Configuration Reference

### Global Settings

| Parameter | Default | Description |
| ----------- | --------- | ------------- |
| `trustName` | `Trust_1` | Name of this trust institution |
| `trustNumber` | `1` | Numeric identifier for this trust |
| `environment` | `production` | Deployment environment (production, stag, dev) |
| `logLevel` | `INFO` | Log level for all services |
| `flBackend` | `nvflare` | FL backend: `nvflare` or `flower` |
| `awsRegion` | `eu-west-2` | AWS region for S3 access |
| `imagePullSecrets` | `[]` | Registry credentials for private images |
| `namespace.create` | `true` | Whether to create the namespace |
| `namespace.name` | `""` | Namespace name (defaults to release namespace) |

### Secrets

| Parameter | Default | Description |
| ----------- | --------- | ------------- |
| `secrets.create` | `false` | Whether the chart creates a Secret resource |
| `secrets.existingName` | `flip-trust-secrets` | Name of existing Secret |
| `secrets.data.*` | `""` | Secret key-value pairs (base64 encoded) |

### Service-Specific Settings

Each service has a configuration block with the following common structure:

```yaml
serviceName:
  enabled: true               # Deploy this service
  image:
    repository: ghcr.io/...   # Container image repository
    tag: stag                 # Image tag
    pullPolicy: Always        # Image pull policy
  replicas: 1                 # Number of pod replicas
  service:
    port: 8000                # Service port
    type: ClusterIP           # Service type (ClusterIP, NodePort, LoadBalancer)
  resources:
    requests:
      memory: "512Mi"
      cpu: "250m"
    limits:
      memory: "1Gi"
      cpu: "500m"
```

Available services:

| Service Block | Description | Stateful? |
| --------------- | ------------- | ----------- |
| `trustApi` | API gateway, polls Central Hub | No |
| `imagingApi` | DICOM image retrieval | No |
| `dataAccessApi` | OMOP database queries | No |
| `flClient` | FL participant | No |
| `omopDb` | OMOP PostgreSQL database | Yes |
| `orthanc` | DICOM PACS server | Yes |
| `xnat.web` | XNAT Tomcat web application | Yes |
| `xnat.db` | XNAT PostgreSQL database | Yes |
| `xnat.nginx` | XNAT reverse proxy | No |
| `observability.loki` | Log aggregation | Yes |
| `observability.alloy` | Log collection agent | No (DaemonSet) |
| `observability.grafana` | Metrics dashboard | Yes |

### External Service Override

Stateful services (`omopDb`, `orthanc`, `xnat`) support external overrides:

```yaml
omopDb:
  enabled: false
  external:
    host: "my-rds-instance.cluster-xxx.eu-west-2.rds.amazonaws.com"
    port: 5432
```

When `enabled: false`, the chart creates an `ExternalName` Service pointing to
the external host instead of deploying the service itself.

### FL Backend Configuration

Switch between NVFLARE and Flower:

```bash
# NVFLARE (default)
helm install trust-release ./ --set flBackend=nvflare

# Flower
helm install trust-release ./ --set flBackend=flower
```

### GPU Configuration

```yaml
flClient:
  gpu:
    enabled: true
    count: 1
```

Requires the [NVIDIA GPU Operator](https://github.com/NVIDIA/gpu-operator) to be
installed in the cluster.

### Autoscaling

```yaml
autoscaling:
  enabled: true
  minReplicas: 1
  maxReplicas: 5
  targetCPUUtilizationPercentage: 80
  targetMemoryUtilizationPercentage: 80
```

### Pod Disruption Budget

```yaml
podDisruptionBudget:
  enabled: true
  minAvailable: 1
```

### Network Policies

Network policies are enabled by default and implement the zero-inbound-trust
model:

- Deny all ingress from outside the namespace
- Allow all intra-namespace communication
- Allow egress to DNS (port 53), HTTPS (port 443), AWS IMDS (169.254.169.254)
- Allow custom egress CIDRs via `networkPolicies.allowedEgressCIDRs`

```yaml
networkPolicies:
  enabled: true
  allowedEgressCIDRs:
    - "10.0.0.0/8"
    - "172.16.0.0/12"
```

## Secrets Reference

The following keys must be present in the Secret (either created by the chart
with `secrets.create=true` or pre-created externally):

| Secret Key | Used By | Description |
| ----------- | --------- | ------------- |
| `aes-key-base64` | trust-api, imaging-api, data-access-api | AES-256 encryption key (base64) |
| `trust-api-key` | trust-api | API key for hub authentication |
| `trust-internal-service-key-header` | trust-api, imaging-api, data-access-api | Header name for trust-internal auth |
| `trust-internal-service-key` | trust-api, imaging-api, data-access-api | Secret key for trust-internal auth |
| `omop-postgres-password` | omop-db | PostgreSQL password |
| `data-access-postgres-password` | data-access-api | Data reader DB password |
| `orthanc-registered-users` | orthanc | Orthanc registered users (JSON) |
| `xnat-admin-password` | xnat-web | XNAT admin password |
| `xnat-service-user` | xnat-web, imaging-api | XNAT service account username |
| `xnat-service-password` | xnat-web, imaging-api | XNAT service account password |
| `xnat-datasource-password` | xnat-web, xnat-db | XNAT database password |
| `grafana-admin-password` | grafana | Grafana admin password |
| `s3-access-key-id` | fl-client (init container) | AWS access key for S3 kit sync |
| `s3-secret-access-key` | fl-client (init container) | AWS secret key for S3 kit sync |

For production, use [External Secrets Operator](https://external-secrets.io/) to
sync secrets from AWS Secrets Manager or HashiCorp Vault.

## Architecture

### Service Dependencies

```
                    ┌─────────────┐
                    │   Hub API   │
                    │  (external) │
                    └──────┬──────┘
                           │ polls
                    ┌──────▼──────┐
                    │  trust-api  │
                    └──┬───┬───┬──┘
                       │   │   │
              ┌────────┘   │   └────────┐
              ▼            ▼            ▼
      ┌────────────┐ ┌─────────┐ ┌──────────────┐
      │imaging-api │ │data-    │ │ fl-client     │
      │            │ │access-  │ │(connects to   │
      │            │ │api      │ │ FL server     │
      └──┬───┬─────┘ └──┬──────┘ │ externally)   │
         │   │          │        └──────────────┘
    ┌────┘   └───┐      │
    ▼            ▼      ▼
┌────────┐ ┌────────┐ ┌────────┐
│orthanc │ │xnat-web│ │omop-db │
│(PACS)  │ │(XNAT)  │ │(OMOP)  │
└────────┘ └───┬────┘ └────────┘
               │
          ┌────┴────┐
          │xnat-db  │
          │(PG)     │
          └─────────┘
```

### Security Model

- **NetworkPolicies**: Default-deny-ingress, allow-intra-namespace, allow-egress
  to Central Hub and FL server only
- **No LoadBalancer or NodePort** for application services (all ClusterIP)
- **Secrets**: Separate from ConfigMaps; recommend External Secrets Operator
- **FL clients**: No Central Hub credentials; connect outbound to FL server only

## Development

### Chart Testing

```bash
# Lint the chart
make -C deploy/providers/kubernetes lint

# Render templates
make -C deploy/providers/kubernetes template

# Test all FL backends
make -C deploy/providers/kubernetes template-all-backends

# Full validation
make -C deploy/providers/kubernetes test
```

### CI Validation

The chart is validated in CI via:

1. `helm lint` — static chart validation
2. `helm template` — template rendering for all backends
3. `helm template` with all services disabled — verifies empty rendering
4. `kubeconform` — schema validation against Kubernetes 1.28+
5. kind-based e2e — deploys the chart to a kind cluster and verifies pods start

## Troubleshooting

### Pods stuck in Pending

| Cause | Check | Fix |
| ------- | ------- | ----- |
| **PVC binding** | `kubectl describe pod <name> -n <ns>` — look for `FailedBinding` events | Ensure a default StorageClass exists or set `persistence.storageClassName` per service. For ReadWriteMany volumes (shared-images), verify the cluster has a RWX-capable provisioner (e.g., EFS, Longhorn, NFS). |
| **Resource limits** | Pod requests may exceed node capacity | Check node resources: `kubectl describe nodes`. Reduce `resources.requests` or add worker nodes. |
| **GPU unschedulable** | `kubectl describe pod <fl-client>` shows `nvidia.com/gpu` in `Status` | Verify NVIDIA GPU Operator is installed. Check node labels: `kubectl get nodes -o json \| jq '.items[].metadata.labels' \| grep nvidia` |
| **Image pull** | Pod event shows `ErrImagePull` or `ImagePullBackOff` | Verify GHCR credentials. Check `imagePullSecrets` config. For private repos ensure `image.tag` exists. |

### Pods in CrashLoopBackOff

| Cause | Check | Fix |
| ------- | ------- | ----- |
| **Missing secrets** | `kubectl logs <pod> -n <ns>` shows auth/connection errors | Verify the Secret exists: `kubectl get secret -n <ns>`. Compare keys against the [Secrets Reference](#secrets-reference). |
| **Bad env vars** | `kubectl exec <pod> -n <ns> -- env` shows empty/wrong URLs | Check ConfigMap values. For trust-api, verify `CENTRAL_HUB_API_URL` is reachable. |
| **DB unreachable** | trust-api / imaging-api logs show DB connection errors | If using external DB: verify `external.host:port` is correct and firewall allows. For in-cluster DB: check the StatefulSet pod is running. |
| **Init container failed** | `kubectl logs <pod> -c <init-container> -n <ns>` | For fl-client: check S3 bucket exists and access keys are valid. For omop-db-init: verify PVC is bound. |

### FL client won't connect

1. **S3 kit download failed**: Check the `kit-init` init container logs. Verify `s3-access-key-id` and `s3-secret-access-key` in the Secret are correct and the bucket path exists.
2. **Kit path mismatch**: Verify `flClient.nvflare.kitFromS3.pathTemplate` or `flClient.flower.kitFromS3.pathTemplate` resolves to a valid S3 path. The `tpl` function renders `.Values.trustName` so ensure `trustName` is set.
3. **Network policy blocking**: Check egress CIDRs allow reaching the Central Hub and FL server. Temporarily disable policies with `--set networkPolicies.enabled=false` to isolate.
4. **GPU not visible**: Verify `nvidia.com/gpu` annotation on the fl-client pod. Check CUDA env vars (`CUDA_VISIBLE_DEVICES`, `NVIDIA_VISIBLE_DEVICES`) are set via `flClient.gpu.enabled: true`.
5. **Flower superlink**: For Flower backend, verify `flClient.flower.superlink` is a reachable gRPC endpoint and root certificates are in the kit.

### Network policy blocking intra-service traffic

Symptoms: trust-api can't reach imaging-api or data-access-api (connection timeout).

1. Check namespace labels: the `allow-intra-namespace` policy uses `namespaceSelector` matching `kubernetes.io/metadata.name: <namespace>`. Verify the label exists.
2. Check if `allowKubeSystemIngress` needs to be enabled for your CNI (e.g., Cilium, Calico with strict policies).
3. Temporarily disable network policies to isolate: `helm upgrade trust-release . --set networkPolicies.enabled=false`
4. Re-enable with `networkPolicies.enabled=true` and add specific `allowedEgressCIDRs` for the Central Hub and FL server.

### XNAT takes very long to start

| Cause | Check | Fix |
| ------- | ------- | ----- |
| **Heap too small** | `kubectl logs <xnat-web-pod> -n <ns>` shows GC/OutOfMemoryError | Increase `xnat.web.env.XNAT_MAX_HEAP` (default `3072m`). For large datasets, set to `4096m` or higher. |
| **DB init** | Postgres init on first deploy loads schema | First start can take 2-5 minutes. Check `xnat-db` pod for `pg_isready` success. |
| **Plugin loading** | XNAT loads plugins at startup | No workaround — plugins are image-baked. Each plugin adds ~30s startup time. |
| **PVC speed** | Slow storage class delays archive/DB I/O | Use SSD-backed storage classes (e.g., `gp3` on EKS, `Premium` on AKS). |

### Orthanc / OMOP init job fails

**Orthanc**:

- Check `orthanc-registered-users` secret — must be valid JSON. Test with `echo '<value>' | python3 -m json.tool`.
- Orthanc uses SQLite embedded DB — `replicas` must stay at 1. The PVC is `ReadWriteOnce`.

**OMOP init job** (`omop-db-init-job`):

- The init Job is a Helm `post-install,post-upgrade` hook that downloads and restores OMOP data from S3.
- If the Job fails: check `s3-bucket` and `s3-path` values. Verify `s3-access-key-id` / `s3-secret-access-key` in the Secret.
- PVC name must match the StatefulSet's `volumeClaimTemplates` — the Job expects a PVC named `<release-name>-omop-db-data`.
- To re-run: `helm upgrade trust-release . --set omopDb.initJob.enabled=true` or delete the Job and let Helm re-create it.

### Getting help

If the above doesn't resolve your issue, please open a GitHub issue at:
<https://github.com/londonaicentre/FLIP/issues/new>

Include:

- `helm version` and `kubectl version` output
- `kubectl describe pod -n <ns>` for the affected pod(s)
- `kubectl logs -n <ns> <pod-name>` output (redact secrets)
- Your `values.yaml` overrides (redact sensitive keys)

## Known Limitations

1. **XNAT Container Service — Job execution not yet wired**: The Container
   Service plugin's native Kubernetes compute backend (available since plugin
   3.2.0) is registered at init time and the `dcm2niix` command is available
   for per-project event subscriptions. However, the storage path for spawned
   container Jobs is not yet plumbed: XNAT's data PVC is `ReadWriteOnce`, so a
   dcm2niix Job would need either `nodeAffinity` onto the XNAT pod's node or a
   `ReadWriteMany` storage class (e.g. NFS/EFS) to mount the same archive/build
   data. Subscriptions are created successfully, but DICOM-to-NIfTI conversion
   will not yet run end-to-end on the K8s deployment until the PVC topology
   for Jobs is finalised.

2. **Orthanc SQLite**: Orthanc uses an embedded SQLite database that cannot be
   shared across multiple pod replicas. The chart configures Orthanc with
   `replicas: 1` and a `ReadWriteOnce` PVC.

3. **Alloy log collection**: In the K8s deployment, Alloy runs as a DaemonSet
   reading pod log files from the host filesystem, replacing the Docker socket
   approach used in the compose deployment.
