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

Ansible playbook and supporting files to provision an on-premises Ubuntu host as a FLIP Trust node. The provisioned host polls the Central Hub (running in AWS) for tasks — all communication is outbound from the trust.

This is the **local provider** counterpart to the [AWS provider](../AWS/README.md), which manages the Central Hub and (optionally) cloud-hosted Trust instances. Together they form the hybrid deployment model described in [docs/hybrid-local-trust-https-plan.md](../../../docs/hybrid-local-trust-https-plan.md).

## Architecture

```sh
         Internet
             │
             ▼
     ┌───────────────┐
     │    AWS CH      │
     │  (Central Hub) │
     └───▲───────▲───┘
         │       │
  polls  │       │  polls
 (HTTPS) │       │ (HTTPS)
         │       │
  ┌──────┴───┐ ┌─┴─────────┐
  │ Trust A   │ │  Trust B   │
  │ (AWS EC2) │ │  (local)   │
  └──────────┘ └───────────┘

  Trusts poll the hub (outbound only)
```

Each local Trust host runs:

| Service | Port | Protocol |
| --- | --- | --- |
| trust-api | 8000 | HTTP (polls hub outbound) |
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

This wrapper target runs the full AWS + local trust provisioning pipeline, updates the trust configuration in AWS Secrets Manager (per-trust `TRUST_API_KEY`, `AES_KEY_BASE64`, and `TRUST_API_KEY_HASHES`), and redeploys Central Hub so the new secret values are loaded.
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
   - Configures UFW firewall to allow FL ports 8002, 8003 **only from the Central Hub IP**
2. Downloads the `Trust_2` FL participant kit from S3 and deploys it to `/opt/flip/services/Trust_2/{startup,local,transfer}` on the trust host.
3. Runs a targeted `terraform apply` to:
   - Add security group rules allowing FL traffic from the local trust's public IP

### Post-provisioning manual steps

1. **Start the trust stack** on the trust host:

   ```bash
   cd trust
   env PROD=stag make up-local-trust-stag
   ```

2. **Verify** the trust can poll the hub (check trust-api logs for successful task polling).

## Communication Model

Trusts poll the Central Hub for tasks over HTTPS — all communication is **outbound from the trust**. The hub never makes inbound requests to trusts. This simplifies networking: no inbound firewall rules or NAT port-forwarding are needed for the trust API port.

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
  -e ch_public_ip=<central-hub-ip>
```

### `requirements.yml`

Ansible Galaxy dependencies:

- `community.general` (>= 8.0.0) — UFW module
- `geerlingguy.docker` — Docker installation role

Install with:

```bash
uv run ansible-galaxy install -r deploy/providers/local/requirements.yml
```

## Home Network Firewall Configuration

### Port Forwarding (NAT)

Since trusts poll the hub outbound, **no inbound port forwarding is needed for the trust API**.

For federated learning, forward FL ports from the router to the trust host:

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

The returned IP should match the router's WAN IP. If they differ, contact the ISP to request a public IP or opt out of CGNAT. Note: CGNAT only affects FL port forwarding — trust API polling works regardless since it's outbound.

### Dynamic Public IP

If the public IP changes (common with residential broadband):

1. Update the Terraform security group for FL ports: `TF_VAR_local_trust_public_ip=<new-ip> make -C deploy/providers/AWS plan apply`

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Trust not polling hub | Trust stack running? (`docker ps` on trust host). Check trust-api logs for polling errors. |
| `Connection timed out` (FL) | Router port forwarding configured for FL ports? ISP blocking? |
| UFW blocking FL connections | `sudo ufw status verbose` on trust host — verify CH IP is allowed on FL ports |
| Ansible `Permission denied` | SSH key correct? User has sudo? `ANSIBLE_BECOME_PASS` set for local mode? |

## Related Documentation

- [AWS Provider README](../AWS/README.md) — Central Hub and cloud Trust deployment
- [Hybrid HTTPS Deployment Guide](../../../docs/hybrid-local-trust-https-plan.md) — Full architecture and design rationale
- [Trust README](../../../trust/README.md) — Trust service stack details and TLS cert generation
- [Deploy README](../../README.md) — General deployment prerequisites (AWS CLI, SSH keys, GHCR login)
