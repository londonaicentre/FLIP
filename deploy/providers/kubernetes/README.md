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

## Prerequisites

- **Kubernetes cluster** 1.28+ (EKS, AKS, or on-prem)
- **Helm** 3.16+
- **kubectl** configured with cluster access
- **NVIDIA GPU Operator** (if GPU workloads are enabled)
- **External Secrets Operator** or **Secrets Store CSI Driver** (recommended for
  production secrets management)

## Quickstart

### 1. Create the required secrets

```bash
# Option A: Create a Secret manually with kubectl
kubectl create namespace flip-trust
kubectl create secret generic flip-trust-secrets \
  --namespace flip-trust \
  --from-literal=aes-key-base64='<your-base64-aes-key>' \
  --from-literal=trust-api-key='<your-trust-api-key>' \
  --from-literal=trust-internal-service-key-header='<your-header-name>' \
  --from-literal=trust-internal-service-key='<your-internal-key>' \
  --from-literal=omop-postgres-password='<your-omop-password>' \
  --from-literal=data-access-postgres-password='<your-data-access-password>' \
  --from-literal=orthanc-registered-users='<your-orthanc-registered-users>' \
  --from-literal=xnat-admin-password='<your-xnat-admin-password>' \
  --from-literal=xnat-service-password='<your-xnat-service-password>' \
  --from-literal=xnat-datasource-password='<your-xnat-datasource-password>' \
  --from-literal=grafana-admin-password='<your-grafana-password>' \
  --from-literal=s3-access-key-id='<your-aws-access-key>' \
  --from-literal=s3-secret-access-key='<your-aws-secret-key>'

# Option B: Use the chart's built-in Secret template (not recommended for prod)
# Set secrets.create=true and pass the values via --set or a separate values file.
```

### 2. Install the chart

```bash
helm install trust-release ./deploy/providers/kubernetes/ \
  --namespace flip-trust \
  --create-namespace \
  --set secrets.existingName=flip-trust-secrets
```

Or using the Makefile:

```bash
make -C deploy/providers/kubernetes deploy
```

### 3. Verify deployment

```bash
kubectl get pods -n flip-trust
kubectl get svc -n flip-trust
kubectl logs -n flip-trust -l app.kubernetes.io/component=trust-api
```

## Configuration Reference

### Global Settings

| Parameter | Default | Description |
|-----------|---------|-------------|
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
|-----------|---------|-------------|
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
|---------------|-------------|-----------|
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
|-----------|---------|-------------|
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

## Known Limitations

1. **XNAT Docker socket**: The XNAT container service plugin that uses
   `/var/run/docker.sock` does not work in Kubernetes. The Docker socket mount
   is not included in the K8s deployment. XNAT features relying on container
   management (e.g., launching pipelines in Docker containers) will not function.

2. **Orthanc SQLite**: Orthanc uses an embedded SQLite database that cannot be
   shared across multiple pod replicas. The chart configures Orthanc with
   `replicas: 1` and a `ReadWriteOnce` PVC.

3. **Alloy log collection**: In the K8s deployment, Alloy runs as a DaemonSet
   reading pod log files from the host filesystem, replacing the Docker socket
   approach used in the compose deployment.
