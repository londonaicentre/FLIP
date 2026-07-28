# e2e-lza — LZA ingress end-to-end test stack

This directory holds an **isolated Terraform root module** that proves the
**workload-account half** of the FLIP LZA landing-zone ingress design with
dummy services, in a **fresh, sealed workload account** — no IGW, no NAT, no
public subnets. It is deliberately separate from the prod/stag root
(`deploy/providers/AWS/`) and from `dev/`: its state lives at its own key
(`flip/e2e-lza/terraform.tfstate`), and nothing here touches any existing
FLIP environment.

The target ingress design is two-tier:

- **Web**: user → CloudFront + WAF (networking account) → internal NLB
  (networking account, TCP:443 passthrough) → central firewall → TGW →
  **internal ALB (workload account, TLS terminates here)** → ECS.
- **FL (no VPN yet)**: trust FL client (internet, mTLS gRPC) →
  internet-facing NLB (networking account, TCP:8002 passthrough) → firewall →
  TGW → **internal NLB (workload account)** → ECS fl-server.

The networking-account half (CloudFront, edge NLBs, TGW, firewall) is owned
by Martin in the private `aicentre-iac` / LZA repo. The contract between the
two halves is the set of SSM parameters this stack publishes under
`/flip-e2e/networking/` (`parameter_store.tf`), mirroring the existing
`/flip/networking/*` pattern in `../parameter_store.tf`.

**Success** = a request originating outside AWS reaches the dummy ECS service
through Martin's edge, and an FL-style TCP connection on :8002 does the same.

## Open questions for the networking account (Martin) — resolve before/during apply

> These are **not** resolved by this stack. Each has a stated default
> implemented here; do not treat the defaults as decisions.

1. **VPC CIDR allocation.** `TF_VAR_vpc_cidr` in `.env.e2e` is a
   **placeholder**. The value must be allocated from the LZA IPAM and must
   not overlap the TGW route domain. **Needs sign-off before `make apply`.**
2. **ECR pull-through cache.** This stack assumes **no** pull-through cache
   exists in the new account (FLIP#749 is the prod plan, not necessarily
   provisioned here) — the operator pushes the echo image by hand (below).
   If pull-through is available, the task definitions could instead point at
   the cache-namespace image URI (e.g.
   `<account>.dkr.ecr.<region>.amazonaws.com/<cache-prefix>/hashicorp/http-echo:latest`).
3. **ALB DNS-sync mechanism.** The web leg only works if the networking side
   resolves `/flip-e2e/networking/alb_dns_name` **on a cadence** and keeps
   the edge NLB's target IPs current — ALB IPs rotate. Confirm the DNS-sync
   Lambda (or equivalent) exists and its cadence. **Without it the web leg
   silently breaks** — that is exactly what the Phase B drift check detects.
4. **FL edge allow-list.** Does the edge FL listener's SG carry today's
   per-trust `/32` allow-list, or does the central firewall take that role?
   Out of scope here (this stack only opens :8002 to
   `networking_ingress_cidrs` + the VPC), but it decides where trust IPs get
   managed later.

## What gets built

| Component | Detail |
|---|---|
| Sealed VPC | `terraform-aws-modules/vpc ~> 6.0`, private subnets only across 2 AZs, no NAT/IGW, DNS hostnames on |
| VPC endpoints | Gateway: `s3`. Interface: `ecr.api`, `ecr.dkr`, `logs`, `ssm`, `ssmmessages`, `ec2messages` — the sealed account's only AWS-API path |
| ECR | `e2e-lza-echo` private repo (operator-pushed `hashicorp/http-echo`) |
| ECS | Fargate cluster + two 0.25 vCPU / 512 MB services: `e2e-web` (`:5678`, replies `e2e-web ok`) and `e2e-fl` (`:8002`, replies `e2e-fl ok`) |
| Web leg | Internal ALB, HTTP:80 → `e2e-web` target group (HTTP health check on `/`) |
| FL leg | Internal NLB, TCP:8002 → `e2e-fl` target group (TCP health check); static ENI private IPs published for the edge |
| Handoff | SSM params under `/flip-e2e/networking/`: `vpc_id`, `private_subnet_ids`, `alb_dns_name`, `nlb_private_ips`, `web_port` (80), `fl_port` (8002) |
| Probe | t3.micro, private subnet, SSM-only (no key pair, no ingress) for in-VPC verification |

## Prerequisites

1. An AWS SSO profile for the **new sealed workload account**
   (`aws configure sso`). The Makefile refuses the `dev`/`stag`/`prod`
   profiles outright.
2. Terraform >= 1.13.1.
3. Docker on the workstation (for the image push).

## Quick start

```bash
cp .env.e2e.example .env.e2e     # fill in profile, state bucket, CIDR (see open question 1)
make create-backend              # once: create the state bucket if needed
make init
make plan                        # review — the operator applies, nothing here auto-applies
make apply
```

After apply, push the echo image (the sealed account cannot pull from Docker
Hub itself). `<account-id>` is the workload account, `<region>` as configured:

```bash
aws ecr get-login-password --region <region> --profile <e2e-profile> \
  | docker login --username AWS --password-stdin <account-id>.dkr.ecr.<region>.amazonaws.com
docker pull --platform linux/amd64 hashicorp/http-echo   # Fargate tasks are X86_64
docker tag hashicorp/http-echo:latest <account-id>.dkr.ecr.<region>.amazonaws.com/e2e-lza-echo:latest
docker push <account-id>.dkr.ecr.<region>.amazonaws.com/e2e-lza-echo:latest
```

Then force both services to pick the image up if they raced the push:

```bash
aws ecs update-service --cluster e2e-lza-cluster --service e2e-web --force-new-deployment --profile <e2e-profile>
aws ecs update-service --cluster e2e-lza-cluster --service e2e-fl  --force-new-deployment --profile <e2e-profile>
```

## Verification runbook

### Phase A — standalone (before Martin attaches anything)

1. Push the image (above), `make apply`, wait for both services to reach
   `RUNNING` (`aws ecs describe-services --cluster e2e-lza-cluster
   --services e2e-web e2e-fl`).
2. SSM into the probe (the exact command is the `SsmCommand` output):

   ```bash
   aws ssm start-session --target <ProbeInstanceId> --profile <e2e-profile>
   curl http://<AlbDnsName>/          # → e2e-web ok
   curl http://<NlbDnsName>:8002/     # → e2e-fl ok
   ```

3. Confirm both target groups healthy:

   ```bash
   aws elbv2 describe-target-health --target-group-arn <e2e-web tg> --profile <e2e-profile>
   aws elbv2 describe-target-health --target-group-arn <e2e-fl tg>  --profile <e2e-profile>
   ```

### Phase B — with the networking side (TGW attached, edge wired)

4. Martin attaches the VPC to the TGW (consuming the SSM params), adds routes
   and firewall rules, points his edge NLB listeners at `nlb_private_ips`
   (:8002) and at the ALB via his DNS-sync mechanism (:80/:443).
5. Set `TF_VAR_networking_ingress_cidrs` in `.env.e2e` (values from Martin)
   and re-`make apply` — only security-group rules change.
6. From outside AWS: `curl` Martin's edge endpoint → `e2e-web ok`;
   `nc -vz <edge> 8002` (or curl) → FL leg proven.
7. **Drift check (critical).** Record the ALB's current ENI IPs:

   ```bash
   aws ec2 describe-network-interfaces \
     --filters Name=description,Values="ELB app/e2e-lza-web-alb/*" \
     --query 'NetworkInterfaces[].PrivateIpAddress' --profile <e2e-profile>
   ```

   Re-test the web leg after ≥ 24–48 h, or after forcing a listener change.
   If it still works, Martin's IP-sync mechanism is real (open question 3).
   The FL leg needs no such check — NLB IPs are static.

### Acceptance

- Phase B steps 6 **and** 7 pass.
- Return traffic is symmetric and the account stays sealed: the probe can
  reach nothing outbound except via the endpoints, and no NAT/IGW ever
  appears in `terraform state list`.

## Follow-up: TLS on the web leg

The web listener is plain HTTP:80 **for the connectivity proof only**. The
target design terminates TLS at this ALB. Once the chain works:

1. Add an ACM certificate for the chosen hostname and swap the listener to
   HTTPS:443 (`ssl_policy = "ELBSecurityPolicy-TLS13-1-3-2021-06"` as in
   `../main.tf`), updating the ALB SG rule and the `web_port` SSM parameter
   to 443.
2. **Deferred because** ACM DNS validation needs the public hosted zone,
   which currently lives in the FLIP-Prod account — moving/ delegating it is
   its own piece of work.

## Teardown

```bash
make destroy
```

The ECR repo has `force_delete = true` (disposable test image), so destroy is
clean even with images present. State stays in the bucket under
`flip/e2e-lza/terraform.tfstate`.
