# FLIP Deployment Guide: Full AWS Stack & On-Premises Trust

> GitHub Issue: [#157 – Enable HTTPS connection between CH and a locally deployed Trust](https://github.com/londonaicentre/FLIP/issues/157)

## Overview

This guide covers two deployment scenarios:

1. **Full AWS deployment** — provision the Central Hub and a cloud-based Trust EC2 from scratch.
2. **Adding an on-premises (local) Trust** — register a locally managed server so the Central Hub can reach it over HTTPS, assuming an AWS deployment already exists.

> **Assumption for scenario 2:** The Central Hub must already be deployed to AWS. If it has not been deployed yet, complete scenario 1 first with `make full-deploy`, following this guide and the instructions in [deploy/providers/AWS/README.md](../deploy/providers/AWS/README.md).

---

## Architecture

```bash
Internet
   │
   │  HTTPS (TRUST_API_PORT)
   ▼
[On-prem border router/firewall]
   │  port-forwarded to trust host
   ▼
[On-prem Trust Host]
 ┌──────────────────────────────────────────────────┐
 │  nginx-tls container  (listens on TRUST_API_PORT) │
 │     │  TLS termination, cert=trust-server.crt     │
 │     ▼                                             │
 │  trust-api container  (internal port 8000)        │
 └──────────────────────────────────────────────────┘

[AWS EC2 – Central Hub]
  flip-api reads FLIP_API secret from Secrets Manager:
    Trust_N-endpoint = https://<trust-public-ip>:<TRUST_API_PORT>
    trust_ca_cert    = <contents of trust-ca.crt>
```

---

## Scenario 1 — Full AWS Deployment (from scratch)

### Prerequisites

| Requirement | Detail |
| --- | --- |
| AWS CLI + SSO configured | Run `make aws-login` |
| GitHub CLI installed | Run `make github-login` |
| Terraform >= 1.13.1 | Or OpenTofu |
| `uv` package manager | [Installation guide](https://docs.astral.sh/uv/guides/install-python/) |
| SSH key pair at `~/.ssh/host-aws` | `ssh-keygen -t ed25519 -f ~/.ssh/host-aws` |
| `.env.stag` / `.env.production` configured | See [deploy/providers/AWS/README.md](../deploy/providers/AWS/README.md) |

### Quick Start

```bash
cd deploy/providers/AWS
make full-deploy PROD=stag   # staging
# or
make full-deploy PROD=true   # production
```

### What `full-deploy` does

| Step | Target | Purpose |
| --- | --- | --- |
| 1 | `github-login` | Authenticate with GitHub CLI |
| 2 | `aws-login` | Authenticate with AWS SSO |
| 3 | `init` | Terraform init with S3 backend |
| 4 | `import-persistent` | Import pre-existing S3 / Cognito / Secrets / ACM resources to prevent replacement |
| 5 | `plan` | Generate execution plan |
| 6 | `apply` | Provision all AWS infrastructure (VPC, EC2s, RDS, ALB, Secrets Manager). The `trust_ca_cert` field is empty at this point. |
| 7 | `update-env` | Refresh `.env` with Terraform outputs (EC2 IPs, DB endpoint, Cognito IDs) |
| 8 | `gen-trust-ec2-certs` | Generate a CA + server TLS certificate for the cloud Trust EC2 using its now-known public IP. Copies `trust-ca.crt` to the Terraform directory so the next apply can embed it into the secret. Deploys server cert + key to the Trust EC2 over SSH. |
| 9 | `plan` | Re-plan: picks up the new `trust-ca.crt` |
| 10 | `apply` | Update Secrets Manager with the real CA certificate |
| 11 | `ssh-config` | Update `~/.ssh/config` with friendly host aliases (`flip`, `flip-trust`) |
| 12 | `ansible-init` | Install Docker, provision directories, deploy participant-kit services on both EC2s |
| 13 | `deploy-centralhub` | Deploy Central Hub containers |
| 14 | `deploy-trust` | Deploy Trust containers, copy TLS certs to Trust EC2 |
| 15 | `status` | Run health checks |

### Individual targets

Run any step standalone for debugging:

```bash
make aws-login
make init
make import-persistent
make plan && make apply
make update-env
make gen-trust-ec2-certs  # re-generate cloud Trust certs (does NOT restart nginx)
make ssh-config
make ansible-init
make deploy-centralhub
make deploy-trust
make status
```

---

## Scenario 2 — Adding an On-Premises Trust

### Prerequisites for on-premises trust

In addition to the general AWS prerequisites above, the on-premises trust host needs:

| Requirement | Detail |
| --- | --- |
| Static public IP (or DDNS) | Must be stable — used as SAN in the TLS certificate |
| Static LAN IP | For border router port-forwarding |
| SSH access from operator workstation | The operator runs Ansible against this host |
| Ubuntu 22.04+ | Other Debian-based distros may work but are untested |
| Internet access on the host | To pull Docker images from GHCR |
| **Border router NAT** | Forward `TRUST_API_PORT` (default `8020`) TCP → trust host LAN IP |

### Quick Start local trust setup

```bash
cd deploy/providers/AWS

# Remote trust host (Ansible connects via SSH)
make add-local-trust \
  LOCAL_TRUST_IP=<trust-public-ip> \
  LOCAL_TRUST_SSH_KEY=~/.ssh/trust_key

# Operator's own workstation as trust host (Ansible runs locally — no SSH required)
make add-local-trust \
  LOCAL_TRUST_IP=<your-outbound-public-ip>
```

> **Running trust on the operator workstation:** Omit `LOCAL_TRUST_SSH_KEY` entirely. Ansible will run tasks directly on the local machine (`ansible_connection=local`). The `LOCAL_TRUST_IP` still supplies the public IP embedded in the TLS certificate SAN and the AWS security group rule.
>
> **sudo-rs note:** sudo-rs uses a non-standard prompt format that Ansible's interactive become mechanism cannot match. Set `ANSIBLE_BECOME_PASS` in your fish terminal **before** running `make`; the playbook uses `become_flags: -S` so sudo-rs reads the password from stdin rather than opening its own TTY prompt:
>
> ```fish
> set -x ANSIBLE_BECOME_PASS (read -s -P 'Sudo password: ')
> ```
>
> The Makefile will error with this instruction if `ANSIBLE_BECOME_PASS` is not set.

`add-local-trust` runs the following steps automatically:

1. Installs the `community.crypto` Ansible collection on the operator workstation.
2. Runs the [`deploy/providers/local/site_local_trust.yml`](../deploy/providers/local/site_local_trust.yml) playbook which:
   - Installs Docker, `openssl`, `ufw` on the trust host.
   - Creates `/opt/flip/`, `/opt/flip/data/trust-1/`, and `/opt/flip/certs/`.
   - Generates a local CA key + cert, a server key, and a CA-signed server certificate, all with the trust public IP as a Subject Alternative Name.
   - Configures `ufw` to **deny all inbound** except SSH (22), and allows `TRUST_API_PORT`, port 8002, and port 8003 only from the Central Hub's public IP.
   - Fetches `trust-ca.crt` back to `trust/certs/trust-ca.crt` on the operator workstation.
3. Copies `trust-ca.crt` to `deploy/providers/AWS/` so Terraform can embed it in the Secrets Manager secret.
4. Runs a targeted `terraform plan` + `apply` to:
   - Add AWS security group rules allowing the local trust's public IP on FL ports 8002 and 8003.
   - Update the `trust_ca_cert` field in the `FLIP_API` Secrets Manager secret with the new CA certificate.

### Manual steps required after `add-local-trust`

These steps cannot be automated because they are either site-specific or require the trust stack to be started.

#### 1. Configure border router NAT (site-specific)

Add a port-forward / DNAT rule on your on-premises router:

| External | Internal | Protocol |
| --- | --- | --- |
| `<public-ip>:TRUST_API_PORT` | `<trust-host-lan-ip>:TRUST_API_PORT` (default `8020`) | TCP |

> If the trust runs directly on the operator workstation and the machine is behind NAT (e.g. a university network), configure the institution's border router or firewall to forward `TRUST_API_PORT` inbound to this machine's LAN IP.

#### 2. Update the Trust endpoint in AWS Secrets Manager

The `FLIP_API` secret's `Trust_1-endpoint` field still points to the cloud Trust EC2. Update it to point to the local trust:

```bash
SECRET_ARN=$(aws secretsmanager list-secrets \
  --query "SecretList[?Name=='FLIP_API'].ARN" \
  --output text --profile <your-aws-profile>)

aws secretsmanager put-secret-value \
  --secret-id "$SECRET_ARN" \
  --secret-string "$(aws secretsmanager get-secret-value \
      --secret-id "$SECRET_ARN" --query SecretString --output text | \
    python3 -c "
import sys, json
s = json.load(sys.stdin)
s['Trust_1-endpoint'] = 'https://<local-trust-public-ip>:${TRUST_API_PORT}'
print(json.dumps(s))
  ")" \
  --profile <your-aws-profile>
```

#### 3. Start the trust stack on the local host

SSH into the trust host and start the production stack:

```bash
# On the trust host, from the FLIP repo root
cd trust
PROD=true make up-trust-1
```

#### 4. Verify the HTTPS connection

```bash
# From the operator workstation (in deploy/providers/AWS/)
make test-local-trust LOCAL_TRUST_IP=<trust-public-ip>
```

Or from within the trust host itself:

```bash
cd trust
make test-trust-https TRUST_HOST=127.0.0.1
```

---

## Firewall Rules Reference

The Ansible playbook configures `ufw` on the trust host with these rules:

| Direction | Protocol | Port | Source | Purpose |
| --- | --- | --- | --- | --- |
| Inbound | TCP | 22 | Any | SSH (operator access) |
| Inbound | TCP | `TRUST_API_PORT` (8020) | CH public IP only | Trust-API HTTPS |
| Inbound | TCP | 8002 | CH public IP only | FL Server (NVFlare/Flower) |
| Inbound | TCP | 8003 | CH public IP only | FL Admin (NVFlare/Flower) |
| Outbound | Any | Any | Any | Docker image pulls, S3, etc. |
| All other inbound | — | — | — | **Denied** |

> The AWS security group on the Central Hub EC2 mirrors this: ports 8002 and 8003 are opened **only from the local trust's public IP** via the Terraform `local_trust_public_ip` variable (set automatically by `make add-local-trust`).

---

## Certificate Rotation

### Cloud Trust EC2

```bash
cd deploy/providers/AWS
make regen-trust-certs   # regenerates, deploys, restarts nginx-tls, updates Secrets Manager
```

### On-Premises Trust

Re-run `add-local-trust` from the operator workstation. The Ansible playbook is idempotent — it regenerates certificates only if the existing ones are absent or expired:

```bash
make add-local-trust \
  LOCAL_TRUST_IP=<trust-public-ip> \
  LOCAL_TRUST_SSH_KEY=~/.ssh/trust_key
```

Then restart `nginx-tls` on the trust host:

```bash
# On the trust host
docker restart trust1-nginx-tls-1
```

---

## Summary Checklist

### Full AWS Deployment

- [ ] Fill in `.env.stag` or `.env.production`
- [ ] Create SSH key: `ssh-keygen -t ed25519 -f ~/.ssh/host-aws`
- [ ] `cd deploy/providers/AWS && make full-deploy PROD=stag`
- [ ] Verify: `make status`

### Adding an On-Premises Trust

- [ ] Obtain static public IP (or DDNS) for the trust host
- [ ] If remote host: ensure SSH access from the operator workstation
- [ ] Configure border router: forward `TRUST_API_PORT` (default `8020`) → trust host LAN IP
- [ ] Remote: `cd deploy/providers/AWS && make add-local-trust LOCAL_TRUST_IP=<ip> LOCAL_TRUST_SSH_KEY=<key>`
- [ ] Operator workstation: `cd deploy/providers/AWS && make add-local-trust LOCAL_TRUST_IP=<outbound-ip>` (omit `LOCAL_TRUST_SSH_KEY`)
- [ ] Update `Trust_1-endpoint` in the `FLIP_API` Secrets Manager secret (see Step 2 above)
- [ ] On trust host: `PROD=true make up-trust-1`
- [ ] Verify: `make test-local-trust LOCAL_TRUST_IP=<ip>`
