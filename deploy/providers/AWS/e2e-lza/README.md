# e2e-lza — LZA ingress end-to-end test stack

This directory holds an **isolated Terraform root module** that proves the
**workload-account half** of the FLIP LZA landing-zone ingress design with
dummy services, inside an **LZA-provisioned sealed workload VPC** — no IGW,
no NAT, no public subnets. It is deliberately separate from the prod/stag
root (`deploy/providers/AWS/`) and from `dev/`: its state lives at its own
key (`flip/e2e-lza/terraform.tfstate`), and nothing here touches any legacy
FLIP environment.

The target ingress design is two-tier:

- **Web**: user → CloudFront + WAF (networking account) → internal NLB
  (networking account, TCP:443 passthrough) → central firewall → TGW →
  **internal ALB (workload account, TLS terminates here)** → ECS.
- **FL (no VPN yet)**: trust FL client (internet, mTLS gRPC) →
  internet-facing NLB (networking account, TCP:8002 passthrough) → firewall →
  TGW → **internal NLB (workload account)** → ECS fl-server.

The networking-account half (CloudFront, edge NLBs, TGW, firewall) is owned
by Martin in the private `londonaicentre/lza` + `aicentre-lza-iac` repos.
The contract between the two halves is the set of SSM parameters this stack
publishes under `/flip-e2e/networking/` (`parameter_store.tf`), mirroring
the existing `/flip/networking/*` pattern in `../parameter_store.tf`.

**Success** = a request originating outside AWS reaches the dummy ECS service
through Martin's edge, and an FL-style TCP connection on :8002 does the same.

## How the LZA estate shapes this stack

Unlike a self-managed account, the LZA platform owns the network layer, so
this root **data-sources** it rather than creating it:

- The VPC comes from the LZA **"prod" VPC template** (applied to every
  account in the `Workloads/Prod` OU): IPAM-allocated CIDR, no IGW, S3 +
  DynamoDB gateway endpoints, a **TGW attachment wired at provision time**,
  and `useCentralEndpoints` (interface endpoints for ec2/ecr/kms/logs/
  monitoring/ssm* live centrally in the Network account and resolve here).
  The `GRNETSEC2` SCP denies VPC/subnet/NAT/IGW/EIP/endpoint creation to
  workload roles, so this stack could not build those even if it wanted to.
- The prod VPC template is **single-AZ today** (`prod-app-a` only). An ALB
  needs ≥ 2 AZs, so the **web leg is gated behind `enable_web_leg`
  (default false)** until the multi-AZ template change lands (open
  question 2). The **FL leg (NLB) is single-AZ-capable and applies now** —
  test it first.
- CloudFront **VPC origins are SCP-denied** for workload roles
  (`GRCLOUDFRONTVPCORIGIN`, lza PR #36) — the legacy prod shortcut
  (CloudFront → VPC origin → internal ALB) is not available in the LZA.
  The two-tier chain this stack tests is the only sanctioned web path.
- The account already carries the migration's out-of-band infra: the
  `flip-terraform-state-lza` state bucket and ECR **pull-through cache
  rules for ghcr.io and public.ecr.aws** (neither covers Docker Hub, so the
  echo image is still hand-pushed — see below).

## Open questions for the networking account (Martin) — resolve before/during Phase B

> These are **not** resolved by this stack. Each has a stated default
> implemented here; do not treat the defaults as decisions.

1. **Which account runs this test?** Default assumption: **FLIPProduction
   (`893493035022`)** — it is empty apart from the migration scaffolding,
   the stack is fully disposable (`make destroy`), and it exercises the
   exact subnets/TGW/firewall path real FLIP will use. The alternative is a
   fresh test account: one `accounts-config.yaml` entry under
   `Workloads/Prod` auto-provisions an identical VPC + TGW attachment, at
   the cost of an account-provisioning cycle. Martin's call.
2. **Multi-AZ subnets.** The prod VPC template is single-AZ; the multi-AZ
   change (requested 2026-07-09) blocks this stack's web-leg ALB and,
   later, prod's ALB + RDS. Once `prod-app-b` exists, set
   `TF_VAR_enable_web_leg=true` and re-apply — the subnet data source picks
   the new subnet up automatically.
3. **ALB DNS-sync mechanism.** The web leg only works if the networking
   side resolves `/flip-e2e/networking/alb_dns_name` **on a cadence** and
   keeps the edge NLB's target IPs current — ALB IPs rotate. Confirm the
   DNS-sync Lambda (or equivalent) exists and its cadence. **Without it the
   web leg silently breaks** — that is exactly what the Phase B drift check
   detects.
4. **FL edge allow-list.** Does the edge FL listener's SG carry today's
   per-trust `/32` allow-list, or does the central firewall take that role?
   Out of scope here (this stack only opens :8002 to
   `networking_ingress_cidrs` + the VPC), but it decides where trust IPs
   get managed later.

Resolved since the original brief: ~~CIDR allocation~~ (the template's IPAM
pool handles it) and ~~ECR pull-through availability~~ (exists for
ghcr/ecr-public; irrelevant for the Docker-Hub-hosted echo image).

## What gets built

| Component | Detail |
|---|---|
| Network layer | **Data-sourced**, never created: LZA prod VPC (`lza_vpc_name`, default `AWSAccelerator-eu-west-2-prod`) + its `*-prod-app-*` subnets |
| ECR | `e2e-lza-echo` private repo (operator-pushed `hashicorp/http-echo`) |
| ECS | Fargate cluster + two 0.25 vCPU / 512 MB services: `e2e-web` (`:5678`, replies `e2e-web ok`; gated with the web leg) and `e2e-fl` (`:8002`, replies `e2e-fl ok`) |
| Web leg (gated) | Internal ALB, HTTP:80 → `e2e-web` target group (HTTP health check on `/`) — `enable_web_leg=true` once multi-AZ lands |
| FL leg | Internal NLB, TCP:8002 → `e2e-fl` target group (TCP health check); static ENI private IPs published for the edge |
| Handoff | SSM params under `/flip-e2e/networking/`: `vpc_id` + `private_subnet_ids` (informational — TGW attachment pre-exists), `alb_dns_name` (once web leg enabled), `nlb_private_ips`, `web_port` (80), `fl_port` (8002) |
| Probe | t3.micro, app subnet, SSM-only (no key pair, no ingress) for in-VPC verification |

## Prerequisites

1. An Identity Center profile for the LZA workload account
   (`aws configure sso`; sso-session separate from the legacy accounts).
   The Makefile refuses the legacy `dev`/`stag`/`prod` profiles and expects
   `FLIPAdminAccess-893493035022` (override via `E2E_AWS_PROFILE`).
2. Terraform >= 1.13.1.
3. Docker on the workstation (for the image push).

## Quick start

```bash
cp .env.e2e.example .env.e2e     # defaults are pre-filled for FLIPProduction
make create-backend              # no-op if flip-terraform-state-lza already exists
make init
make plan                        # review — the operator applies, nothing here auto-applies
make apply
```

After apply, push the echo image (the sealed account cannot pull from Docker
Hub itself — the LZA pull-through rules cover ghcr/ecr-public only).
`<account-id>` is the workload account, `<region>` as configured:

```bash
aws ecr get-login-password --region <region> --profile <e2e-profile> \
  | docker login --username AWS --password-stdin <account-id>.dkr.ecr.<region>.amazonaws.com
docker pull --platform linux/amd64 hashicorp/http-echo   # Fargate tasks are X86_64
docker tag hashicorp/http-echo:latest <account-id>.dkr.ecr.<region>.amazonaws.com/e2e-lza-echo:latest
docker push <account-id>.dkr.ecr.<region>.amazonaws.com/e2e-lza-echo:latest
```

Then force the service(s) to pick the image up if they raced the push:

```bash
aws ecs update-service --cluster e2e-lza-cluster --service e2e-fl  --force-new-deployment --profile <e2e-profile>
# and once the web leg is enabled:
aws ecs update-service --cluster e2e-lza-cluster --service e2e-web --force-new-deployment --profile <e2e-profile>
```

## Verification runbook

### Phase A — standalone (before Martin wires the edge)

1. Push the image (above), `make apply`, wait for the service(s) to reach
   `RUNNING` (`aws ecs describe-services --cluster e2e-lza-cluster
   --services e2e-fl e2e-web`).
2. SSM into the probe (the exact command is the `SsmCommand` output):

   ```bash
   aws ssm start-session --target <ProbeInstanceId> --profile <e2e-profile>
   curl http://<NlbDnsName>:8002/     # → e2e-fl ok
   curl http://<AlbDnsName>/          # → e2e-web ok   (once enable_web_leg=true)
   ```

3. Confirm the target group(s) healthy:

   ```bash
   aws elbv2 describe-target-health --target-group-arn <e2e-fl tg>  --profile <e2e-profile>
   aws elbv2 describe-target-health --target-group-arn <e2e-web tg> --profile <e2e-profile>
   ```

### Phase B — with the networking side (edge wired)

4. Martin consumes the SSM params and points his edge listeners at
   `nlb_private_ips` (:8002) and at the ALB via his DNS-sync mechanism
   (:80/:443), with firewall rules to match. (The TGW attachment already
   exists — the LZA VPC template created it.)
5. Set `TF_VAR_networking_ingress_cidrs` in `.env.e2e` (values from Martin)
   and re-`make apply` — only security-group rules change.
6. From outside AWS: `nc -vz <edge> 8002` (or curl) → FL leg proven;
   `curl` Martin's edge endpoint → `e2e-web ok` → web leg proven.
7. **Drift check (critical, web leg).** Record the ALB's current ENI IPs:

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
   which currently lives in the legacy FLIP-Prod account — moving/
   delegating it is its own piece of work.

## Teardown

```bash
make destroy
```

The ECR repo has `force_delete = true` (disposable test image), so destroy is
clean even with images present. The LZA-provisioned network layer is
untouched by destroy (it was only ever data-sourced). State stays in the
bucket under `flip/e2e-lza/terraform.tfstate`.
