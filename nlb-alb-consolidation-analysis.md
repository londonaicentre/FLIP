# Evaluate NLB/ALB Consolidation for FLIP

## Context

FLIP currently uses **two load balancers** in AWS:
- **ALB** (`flip-alb`): HTTP/HTTPS traffic — UI (443), API (8080), FL API (8000)
- **NLB** (`flip-fl-server-nlb`): TCP passthrough — FL server gRPC (8002)

The question: can we eliminate the NLB and route all traffic through a single ALB?

## Analysis

### What the NLB handles today

Port 8002 — raw TCP passthrough for FL server↔client communication. No TLS termination at the LB level. The NLB acts as a transparent Layer 4 proxy.

### Protocol details by FL framework

| Framework | Protocol on port 8002 | TLS/mTLS | Can ALB handle it? |
|-----------|----------------------|----------|-------------------|
| **NVFLARE** | gRPC with **mTLS** (provisioned certificates) | Yes — server and client validate each other's certs end-to-end | **No** |
| **Flower** | gRPC with `--insecure` flag (plain, no TLS) | No — relies on network isolation | **Maybe** (with HTTP/2 target group) |

### Why ALB **cannot** replace NLB for NVFLARE

1. **mTLS requirement**: NVFLARE provisions its own certificate bundle. Server validates client certs; clients validate server certs. This is end-to-end mTLS at the application layer.
2. **ALB always terminates TLS**: ALB operates at Layer 7 and terminates TLS connections. It cannot pass through the original TLS session to the backend. This breaks NVFLARE's mTLS chain — the server would see the ALB's connection, not the client's certificate.
3. **No TCP passthrough on ALB**: ALB only supports HTTP/HTTPS/gRPC (all Layer 7). It cannot do raw TCP passthrough like NLB.

### Could ALB work for Flower?

Not long-term. Currently Flower uses `--insecure` (plain gRPC), so ALB's native gRPC/HTTP/2 support could technically handle it today. However:
- **Flower will move to secure/mTLS connections** in the future, replacing the `--insecure` flag — at that point it will have the same NLB requirement as NVFLARE
- FLIP supports **both** NVFLARE and Flower (configurable via `FL_BACKEND`), so the NLB is needed for NVFLARE regardless
- Maintaining two different LB configurations per FL backend adds unnecessary complexity

### Cost consideration

An NLB is relatively cheap (~$16-22/month base + data charges). The operational simplicity of keeping one LB per traffic type outweighs the modest cost saving.

## Conclusion

**No — the NLB cannot be eliminated.** The current architecture is correct.

- **NVFLARE requires TCP passthrough** for its mTLS gRPC communication. ALB cannot provide this.
- **Flower currently uses `--insecure` gRPC** but will move to mTLS in the future, so it will also need TCP passthrough.
- The NLB's TCP passthrough is the right choice because it works transparently for both frameworks regardless of their TLS configuration.

Your original intuition was right — the FL server-client communication doesn't use HTTP, and for NVFLARE specifically, it requires end-to-end mTLS that ALB would break.

### Key files referenced

- `deploy/providers/AWS/main.tf` (lines 292-420) — ALB and NLB definitions
- `deploy/compose.production.nvflare.yml` — NVFLARE cert volume mounts
- `deploy/compose.production.flower.yml` — Flower `--insecure` flag
- `deploy/providers/AWS/variables.tf` — port variable definitions
