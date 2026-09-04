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

# NetworkPolicy audit & threat model (#516)

This documents what the chart's NetworkPolicies (`templates/network-policy.yaml`,
gated by `networkPolicies.enabled`, default `true`) actually allow, **why** each
rule exists, the residual risk, and how to harden further. It is the audit asked
for in [#516](https://github.com/londonaicentre/FLIP/issues/516).

## Trust model

The trust runs untrusted-to-the-cluster workloads (researcher-authored FL
training code runs in the fl-client) over sensitive data (OMOP, DICOM). The
NetworkPolicies enforce the platform's **zero-inbound-trust** posture: nothing
outside the namespace may dial in, and pods may only egress where they must.
They are a *defence-in-depth* control, **not** the primary data-exfiltration
boundary — see [Residual risk](#residual-risk).

> Requires a CNI that enforces NetworkPolicy (Calico, Cilium, Antrea, …). On a
> CNI that ignores them (e.g. default flannel) these objects render but are not
> enforced — verify with a deny test before relying on them.

## Policies emitted

| Policy | Type | Effect |
|---|---|---|
| `*-default-deny-ingress` | Ingress | `podSelector: {}` with no `ingress:` rules ⇒ **deny all inbound** to every pod. Optionally allows `kube-system` (kubelet probes/metrics) when `allowKubeSystemIngress=true` (default `false`). |
| `*-allow-intra-namespace` | Ingress | Allows inbound **only** from pods in the same namespace (`namespaceSelector` on `kubernetes.io/metadata.name`). This is what lets trust-api reach imaging-api / data-access-api. |
| `*-egress` | Egress | Default-deny egress with the allowlist below. |

### Egress allowlist (audited)

| Rule | Destination | Why it exists | Risk if abused |
|---|---|---|---|
| `allowedEgressPorts` (default 53/UDP, 53/TCP, 80/TCP, 443/TCP) | **any IP** | DNS resolution; 443 for the hub poll (CloudFront), S3 (kit/results), Cognito, GHCR/ECR image pulls; 80 for redirects/package metadata. `sync-kit` appends `FL_SERVER_PORT` here for the fl-client → fl-server gRPC (#593 pt.3, port-only). | **Primary residual risk: 443/80 to any IP is an exfiltration channel.** A compromised fl-client could POST data anywhere on 443. The added FL-server port widens egress on that one port to any IP — accepted because the FL server is behind an internet-facing NLB with rotating AWS-managed IPs that a `/32` pin cannot track. |
| intra-namespace | same namespace | trust-api → imaging/data-access/fl-client, etc. | Low — intra-trust only. |
| `allowedIngressCIDRsWithPorts` (default `[]`) | listed CIDRs, one port, **inbound** to xnat-web | The DICOM C-STORE return leg. FLIP pulls, so after XNAT issues C-MOVE the PACS opens a new association back to XNAT; without this it is dropped and retrievals silently time out (FLIP#993). | Scope to the PACS itself, never the whole trust network. Default-deny is unchanged while the list is empty. |
| `allowedEgressCIDRs` (default `[]`) | listed CIDRs, **all ports** | Operator escape hatch to reach an external OMOP/PACS/XNAT on arbitrary ports. | Scoped to listed CIDRs; all-ports is broad — keep the list tight. |
| `allowedEgressCIDRsWithPorts` (default `[]`) | listed CIDRs, one port | Operator escape hatch for CIDR+port egress (e.g. an on-prem service on a fixed IP). Not populated by `sync-kit` — the FL-server allowance is port-only (see `allowedEgressPorts`). | Scoped CIDR+port — tightest rule. |
| AWS IMDS | `169.254.169.254/32` | EC2 metadata / IAM-role credentials for the fl-client S3 kit sync. | IMDS is a known SSRF/cred-theft target — see hardening note. |

## Residual risk

1. **443/80-to-anywhere egress.** The dominant gap. Locking it down is *not* a
   safe default: the trust must reach CloudFront (the hub — a large, shifting
   AWS edge range), S3, Cognito, and GHCR/ECR, none of which have small stable
   CIDRs. A naive `443 → hub-CIDR-only` policy breaks polling and image pulls.
   Mitigation options, in increasing strength:
   - **Restrict by CIDR where the environment allows** (e.g. a private-link /
     VPC-endpoint-only deployment): set `allowedEgressPorts` to DNS only and
     route hub/S3/Cognito through `allowedEgressCIDRs` pinned to the endpoint
     IPs.
   - **FQDN-aware egress** (Cilium `CiliumNetworkPolicy` `toFQDNs`, or an egress
     proxy/Squid allowlisting `*.aicentre.co.uk`, `*.amazonaws.com`): the only
     way to safely allow 443 to *named* hosts rather than any IP. Out of scope
     for a CNI-portable chart; recommended for high-security trusts.
2. **IMDS reachable from all pods.** Only the fl-client init container needs it.
   A per-pod tightening (allow IMDS egress only on the fl-client `podSelector`,
   deny elsewhere) is a future hardening — pairs with IMDSv2 hop-limit=1 and the
   RBAC/securityContext work in [#530](https://github.com/londonaicentre/FLIP/issues/530).
3. **NetworkPolicy is L3/L4 only.** It cannot inspect payloads; it is not a DLP
   control. Treat the data-governance boundary as the trust perimeter itself.

## Hardening example

Restrict all-ports egress to a known external data service and drop 80, keeping
DNS + 443 for the hub:

```yaml
networkPolicies:
  enabled: true
  allowedEgressPorts:
    - { port: 53, protocol: UDP }
    - { port: 53, protocol: TCP }
    - { port: 443, protocol: TCP }   # hub / S3 / Cognito / registry
  allowedEgressCIDRs:
    - "10.20.0.0/24"                  # e.g. on-prem OMOP/PACS subnet
  allowedEgressCIDRsWithPorts:
    - { cidrs: ["13.43.162.56/32", "13.43.208.193/32"], port: 8002 }  # fl-server NLB
```

## CI coverage

`make -C deploy/providers/kubernetes template-egress-variants` renders the
egress policy in three shapes (default, hardened-CIDRs, policies-disabled) so a
change that breaks non-default egress configs is caught in review. Wire it into
`test_helm_chart.yml` alongside `template-all-backends`.

## Verifying enforcement (manual)

```bash
# From a pod in the namespace, egress on an unlisted port must FAIL (timeout):
kubectl -n flip-trust exec deploy/trust-release-flip-trust-trust-api -- \
  sh -c 'timeout 5 nc -z example.com 22; echo exit=$?'   # expect non-zero
# Inbound from another namespace must FAIL; DNS + 443 to the hub must succeed.
```
