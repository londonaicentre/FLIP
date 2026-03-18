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

# FLIP Local (On-Premises) Trust Deployment

Ansible playbook and supporting files to provision an on-premises Ubuntu host as a FLIP Trust node, secured with HTTPS via an nginx-tls sidecar. The provisioned host connects back to a Central Hub running in AWS.

This is the **local provider** counterpart to the [AWS provider](../AWS/README.md), which manages the Central Hub and (optionally) cloud-hosted Trust instances. Together they form the hybrid deployment model described in [docs/hybrid-local-trust-https-plan.md](../../../docs/hybrid-local-trust-https-plan.md).

## Architecture

```sh
        Internet
            │
            |
            │
            ▼
     ┌──────────┐             ┌──────────┐
     │ AWS CH    │             │ On Prem   │
     │ (Central  │-------------│ Router    │
     │  Hub)     │     │       │ NAT fwd   │
     └────┬─────┘     │       └────┬─────┘
          │            │            │
    HTTPS │            │      HTTPS │
          │            │            │
     ┌────▼─────┐     │       ┌────▼────┐
     │ Trust A   │     │       │ Trust B  │
     │ (AWS EC2) │     │       │ (local)  │
     └──────────┘     |       └─────────┘
                       │
              All links use HTTPS
          (self-signed CA + nginx-tls)
```

Each local Trust host runs:

| Service | Port | Protocol |
| --- | --- | --- |
| **nginx-tls** | `TRUST_API_PORT` (default 8020) | HTTPS (external) |
| trust-api | 8000 | HTTP (internal, behind nginx) |
| imaging-api | 8000 | HTTP (internal) |
| data-access-api | 8000 | HTTP (internal) |
| fl-client | 8002, 8003 | TCP (FL server/admin) |

## Prerequisites

1. **Operator workstation** — The machine where you run the commands (typically your laptop). It needs:
   - Python 3.12+ and [UV](https://docs.astral.sh/uv/guides/install-python/)
   - Ansible (installed via `uv sync` in `deploy/providers/AWS/`)
   - Terraform outputs available (you must have run `make init` + `make apply` in `deploy/providers/AWS/` first)
   - SSH access to the trust host (if provisioning remotely)

2. **Trust host** — An Ubuntu 22.04+ machine (physical or VM) with:
   - A user account with `sudo` privileges (default: `ubuntu`)
   - SSH access from the operator workstation (if remote), or local access
   - Internet connectivity (to pull Docker images and packages)

3. **AWS Central Hub deployed** — The Central Hub must be running in AWS so that Terraform can provide the CH public IP for firewall rules. See [`deploy/providers/AWS/`](../AWS/README.md).

4. **Home network access** — The ability to configure port forwarding on the border router.

## Quick Start

All commands are run from the `deploy/providers/AWS/` directory since the Makefile targets there orchestrate both cloud and local infrastructure:

```bash
cd deploy/providers/AWS
```

### Recommended end-to-end target (staging hybrid)

```bash
make full-deploy-stag-hybrid LOCAL_TRUST_IP=<public-ip> [LOCAL_TRUST_SSH_KEY=~/.ssh/trust_key]
```

This wrapper target runs the full AWS + local trust provisioning pipeline, updates `Trust_1-endpoint` in `FLIP_API`, and redeploys Central Hub so the new secret values are loaded.
You still need to start the local trust stack on the trust host:

```bash
cd trust
env PROD=stag make up-local-trust-stag
```

### Provision a remote trust host (via SSH)

```bash
make add-local-trust \
  LOCAL_TRUST_IP=<public-ip> \
  LOCAL_TRUST_SSH_KEY=~/.ssh/trust_key
```

### Provision the local machine as the trust host (no SSH)

```bash
# Set the sudo password (fish shell)
set -x ANSIBLE_BECOME_PASS (read -s -P 'Sudo password: ')

make add-local-trust LOCAL_TRUST_IP=<public-ip>
```

### What `add-local-trust` does

1. Runs the Ansible playbook (`site_local_trust.yml`) which:
   - Installs Docker and required system packages
   - Generates a self-signed CA and server TLS certificate with the host's public IP as SAN
   - Configures UFW firewall to allow `TRUST_API_PORT`, 8002, 8003 **only from the Central Hub IP**
   - Fetches the local trust CA certificate back to `trust/certs/local-trust-ca.crt`
2. Creates a **CA bundle** (`deploy/providers/AWS/trust-ca.crt`) by concatenating the cloud Trust EC2 CA (generated earlier by `gen-trust-ec2-certs`) with the new local trust CA. This lets `flip-api` verify HTTPS connections to **both** trusts using a single bundle.
3. Downloads the `Trust_2` FL participant kit from S3 and deploys it to `/opt/flip/services/Trust_2/{startup,local,transfer}` on the trust host.
4. Runs a targeted `terraform apply` to:
   - Upload the CA bundle to Secrets Manager (`trust_ca_cert` key in `FLIP_API` secret)
   - Add security group rules allowing FL traffic from the local trust's public IP

### Post-provisioning manual steps

1. **Configure router port forwarding** — Forward `TRUST_API_PORT/tcp` (default 8020) from the router's WAN interface to the trust host's LAN IP. Optionally forward 8002/tcp and 8003/tcp for federated learning.

2. **Update the trust endpoint in Secrets Manager** — Recommended helper target:

   ```bash
   make set-local-trust-endpoint LOCAL_TRUST_IP=<public-ip>
   ```

3. **Start the trust stack** on the trust host:

   ```bash
   cd trust
   env PROD=stag make up-local-trust-stag
   ```

4. **Verify (CA-validated)** from the operator workstation:

   ```bash
   make test-local-trust LOCAL_TRUST_IP=<public-ip>
   ```

5. **Restart flip-api** on the Central Hub to pick up the new CA cert from Secrets Manager.

## Certificate Model (Critical)

There are two different certificate verification contexts in this hybrid setup:

1. **Central Hub -> Trust API runtime path (must verify CA)**
   - `flip-api` verifies trust HTTPS endpoints using a CA bundle from `FLIP_API.trust_ca_cert`.
   - That bundle is written on CH EC2 to `/opt/flip/certs/trust-ca.crt` and mounted into `flip-api`.
   - The bundle must contain both:
     - cloud Trust EC2 CA (`trust/certs/trust-ca.crt`), and
     - local trust CA (`trust/certs/local-trust-ca.crt`).

2. **Operator/external diagnostics path (must be CA-validated)**
   - `make test-local-trust` uses `--cacert ../../../trust/certs/local-trust-ca.crt`.

If `make test-local-trust` fails with certificate errors, re-run `make add-local-trust` to regenerate/fetch CA certs and rebuild the bundle.

## Ansible Playbook Details

### `site_local_trust.yml`

The main playbook. It can be run standalone or via the `add-local-trust` Makefile target.

**Required variables** (passed with `-e`):

| Variable | Description | Example |
| --- | --- | --- |
| `trust_public_ip` | Public IP (or hostname) of the trust host | `82.1.2.3` |
| `ch_public_ip` | Public IP of the AWS Central Hub EC2 | `18.130.1.2` |

**Optional variables:**

| Variable | Default | Description |
| --- | --- | --- |
| `trust_api_port` | `8443` | Port for the nginx-tls HTTPS listener |
| `cert_dir` | `/opt/flip/certs` | Directory for TLS certificates on the trust host |
| `flip_dir` | `/opt/flip` | Root application directory |

**Direct usage** (without the Makefile):

```bash
cd deploy/providers/AWS
uv run ansible-galaxy install -r ../../../deploy/providers/local/requirements.yml

uv run ansible-playbook \
  -i <trust-host-ip>, \
  -u ubuntu \
  --private-key ~/.ssh/trust_key \
  ../../../deploy/providers/local/site_local_trust.yml \
  -e trust_public_ip=<public-ip> \
  -e ch_public_ip=<central-hub-ip> \
  -e trust_api_port=8020
```

### `requirements.yml`

Ansible Galaxy dependencies:

- `community.general` (>= 8.0.0) — UFW module
- `community.crypto` (>= 2.0.0) — TLS certificate generation
- `geerlingguy.docker` — Docker installation role

Install with:

```bash
uv run ansible-galaxy install -r deploy/providers/local/requirements.yml
```

## Home Network Firewall Configuration

### Port Forwarding (NAT)

Configure a port-forwarding rule on the home router:

| Setting | Value |
| --- | --- |
| **External port** | `8020` (or `TRUST_API_PORT`) |
| **Internal IP** | Trust host LAN IP (e.g. `192.168.1.100`) |
| **Internal port** | `8020` |
| **Protocol** | TCP |

For federated learning, also forward:

| External Port | Internal Port | Protocol | Purpose |
| --- | --- | --- | --- |
| `8002` | `8002` | TCP | FL Server gRPC |
| `8003` | `8003` | TCP | FL Admin |

### Static LAN IP

Assign a static LAN IP to the trust host (via DHCP reservation on the router or static network config on the host) so port forwarding rules survive reboots.

### ISP / CGNAT Check

Some residential ISPs use Carrier-Grade NAT (CGNAT), which prevents inbound port forwarding. To check:

```bash
curl ifconfig.me
```

The returned IP should match the router's WAN IP. If they differ, contact the ISP to request a public IP or opt out of CGNAT.

### Dynamic Public IP

If the public IP changes (common with residential broadband):

1. Regenerate TLS certificates: `TRUST_HOST=<new-ip> make -C trust generate-trust-certs`
2. Copy certs to `/opt/flip/certs/` and restart `nginx-tls`
3. Re-run `make add-local-trust LOCAL_TRUST_IP=<new-ip>` to refresh certs and CA bundle
4. Update endpoint URL in Secrets Manager: `make set-local-trust-endpoint LOCAL_TRUST_IP=<new-ip>`
5. Update the Terraform security group: `TF_VAR_local_trust_public_ip=<new-ip> make -C deploy/providers/AWS plan apply`

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `Connection refused` on HTTPS test | Trust stack running? (`docker ps` on trust host) |
| `Connection timed out` | Router port forwarding configured? ISP blocking the port? |
| `SSL certificate verify failed` | CA cert mismatch — re-run `make add-local-trust` to regenerate and re-distribute |
| UFW blocking connections | `sudo ufw status verbose` on trust host — verify CH IP is allowed |
| Ansible `Permission denied` | SSH key correct? User has sudo? `ANSIBLE_BECOME_PASS` set for local mode? |

## Related Documentation

- [AWS Provider README](../AWS/README.md) — Central Hub and cloud Trust deployment
- [Hybrid HTTPS Deployment Guide](../../../docs/hybrid-local-trust-https-plan.md) — Full architecture and design rationale
- [Trust README](../../../trust/README.md) — Trust service stack details and TLS cert generation
- [Deploy README](../../README.md) — General deployment prerequisites (AWS CLI, SSH keys, GHCR login)
