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

This is the **local provider** counterpart to the [AWS provider](../AWS/README.md), which manages the Central Hub and
(optionally) cloud-hosted Trust instances. Together they implement the
[hybrid deployment model](../../../docs/source/deploy-flip.rst).

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

| Service | Container port | Protocol |
| --- | --- | --- |
| trust-api | 8000 | HTTP (polls hub outbound) |
| imaging-api | 8000 | HTTP (internal) |
| data-access-api | 8000 | HTTP (internal) |
| fl-client | — | TCP (connects outbound to FL server via NLB) |

The on-prem trust kit (`trust/.env.<CODE>.production`) can default `IMAGING_API_PORT` / `DATA_ACCESS_API_PORT` / `TRUST_API_PORT` to host ports shifted off the first dev trust's allocation (`8005` / `8014` / `8024` vs the dev `8001` / `8010` / `8020`), so the prod-pointed stack coexists on a developer laptop with `make up` (whose first dev trust binds the original ports via `trust/deploy/compose_trust-1_override.yml`). A real on-prem operator on a dedicated host can revert the kit-file values to standard `8001` / `8010` / `8020` if their tooling expects them.

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

3. **AWS Central Hub deployed** — The Central Hub must be running in AWS (required for Terraform outputs, FL participant kits in S3, and NLB security group configuration). See [`deploy/providers/AWS/`](../AWS/README.md).

## Quick Start

All commands are run from the `deploy/providers/AWS/` directory since the Makefile targets there orchestrate both cloud and local infrastructure:

```bash
cd deploy/providers/AWS
```

### Recommended end-to-end target (hybrid)

```bash
make full-deploy-hybrid PROD=<stag|true> [LOCAL_TRUST_IP=<public-ip>]
```

This wrapper target runs the full AWS + on-prem trust provisioning pipeline and registers the trust on the running hub (`make register-trusts`) — inserting the `trust` row with its `api_key_hash`, claiming an FL kit slot, and writing the per-trust kit file. No hub redeploy is needed. `PROD` is inherited from the environment and supports both staging (`stag`) and production (`true`). Omit `LOCAL_TRUST_IP` to auto-detect the operator machine's public IP.

You still need to start the on-prem trust stack on the trust host. The playbook
deliberately does **not** add the SSH login user to the `docker` group (docker
group membership is equivalent to root on the host — any member can mount `/`
into a container and chroot in), so docker commands and `make -C trust up-trust`
must be run via `sudo`:

```bash
cd ../../..
sudo -E env PROD=<stag|true> make -C trust up-trust KIT=<CODE>
```

Use the trust code you registered, whose kit file is `trust/.env.<CODE>.production`.
`-E` is load-bearing: it preserves `$HOME`, so root's docker client reuses your
`~/.docker/config.json` GHCR login for the image pulls — a plain `sudo` looks in
`/root/.docker` and the pulls fail. (`PROD` needs no preserving; `env PROD=…` sets
it explicitly for the command.) Any direct `docker`, `docker compose`, or
`docker swarm` invocations on the trust host should likewise be prefixed with `sudo`.

> **Hosts provisioned before this change are not remediated by re-running the
> playbook.** `geerlingguy.docker`'s `docker_users` is additive — it only ever
> adds to the `docker` group, never removes — so dropping the setting stops *new*
> grants but leaves membership a host already picked up in place. Affected hosts
> (the `Trust_2`/BDMS on-prem host among them) are remediated by the planned
> trust reprovisioning, which rebuilds them from this playbook. To close the gap
> sooner on a host that is already up, evict the user directly and have them log
> back in for it to take effect:
>
> ```bash
> sudo gpasswd -d <login-user> docker   # verify with: id -nG <login-user>
> ```

### Provision the trust host

Run this **on the trust host** — there is no SSH path. The target lives in the
AWS provider Makefile (which orchestrates both cloud and local infrastructure),
so run it from `deploy/providers/AWS/`:

```bash
# Set the sudo password (fish shell; bash: read -rsp ... && export ANSIBLE_BECOME_PASS)
set -x ANSIBLE_BECOME_PASS (read -s -P 'Sudo password: ')

cd deploy/providers/AWS       # first time only, if you didn't `cd` earlier
make provision-local-trust
```

### What `provision-local-trust` does

1. Runs the Ansible playbook (`site_local_trust.yml`) which:
   - Installs Docker and required system packages
   - Creates application directories under `/opt/flip/`
2. Downloads the FL participant kit from S3 and stages it under `/tmp`, printing the `sudo rsync` commands to deploy it into `${FL_KIT_DIR}/net-1/...` (default `/opt/flip/fl-kit/net-1/...`).

Opening the AWS FL-server NLB to the trust's public IP is a **separate** step — `make allow-local-trust-nlb LOCAL_TRUST_IP=<public-ip>` — run by the FLIP admin once the operator reports their IP.

### Post-provisioning manual steps

1. **Start the trust stack** on the trust host. The login user is intentionally
   **not** in the `docker` group (see the security note in the recommended
   end-to-end target above), so use `sudo`:

   ```bash
   cd ../../..
   sudo -E env PROD=stag make -C trust up-trust KIT=<CODE>   # the trust code you registered
   ```

2. **Verify** the trust can poll the hub (check trust-api logs for successful task polling — `sudo docker compose logs trust-api`).

## Communication Model

Trusts poll the Central Hub for tasks over HTTPS — all communication is **outbound from the trust**. The hub never makes inbound requests to trusts. This simplifies networking: no inbound firewall rules or NAT port-forwarding are needed for the trust API or FL ports.

## Trust Authentication

Any machine with the correct credentials can act as a trust — the hub identifies trusts by API key, not by IP address or hostname. The trust's `.env` file must have:

| Variable | Purpose |
| --- | --- |
| `TRUST_API_KEY` | Per-trust secret key, from the trust's kit file written by `make register-trusts` |
| `CENTRAL_HUB_API_URL` | Hub URL the trust polls (e.g. `https://app.flip.aicentre.co.uk`) |
| `AES_KEY_BASE64` | Shared encryption key for trust-hub payloads |

**Hub-side prerequisites** (before the trust can connect):

1. The trust must be registered on the hub — run `make register-trusts`, or use the Add-Trust button on the Connection status page. Registration inserts the `trust` row with its `api_key_hash` and claims an FL kit slot. No hub redeploy is needed.

The `full-deploy-with-local-trust` / `full-deploy-hybrid` targets handle trust registration automatically (both honour `PROD=stag|true`). When provisioning a trust standalone with `provision-local-trust`, the trust must already be registered.

## Ansible Playbook Details

### `site_local_trust.yml`

The main playbook. It can be run standalone or via the `provision-local-trust` Makefile target.

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
  ../../../deploy/providers/local/site_local_trust.yml
```

### `requirements.yml`

Ansible Galaxy dependencies:

- `geerlingguy.docker` — Docker installation role

Install with:

```bash
uv run ansible-galaxy install -r deploy/providers/local/requirements.yml
```

## Home Network Firewall Configuration

### Port Forwarding (NAT)

**No inbound port forwarding is needed.** Trusts poll the hub outbound for tasks, and FL clients connect outbound to the FL server via the NLB. All communication is trust-initiated.

The host/network firewall must allow outbound to **two separate Central Hub hosts** (different load balancers — both must be allowlisted):

- **`app.flip.aicentre.co.uk`** — the ALB (HTTPS API, port 443).
- **`fl.app.flip.aicentre.co.uk`** — the NLB (FL gRPC, default port 8002).

### Dynamic Public IP

The NLB security group allowlists the trust's public IP for FL traffic. If the public IP changes (common with residential broadband), update it. Set `LOCAL_TRUST_PUBLIC_IPS` (an HCL list of every allowlisted IP) in the env file and reconcile with `allow-local-trust-nlb`:

```bash
LOCAL_TRUST_PUBLIC_IPS='["<new-ip>"]' make -C deploy/providers/AWS allow-local-trust-nlb LOCAL_TRUST_IP=<new-ip>
```

Re-running with an already-listed IP is a no-op (idempotent).

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Trust not polling hub | Trust stack running? (`sudo docker ps` on trust host). Check trust-api logs for polling errors. |
| `Connection timed out` (FL) | Trust's public IP changed? Update NLB security group. Host/router firewall blocking outbound on port 8002? |
| Firewall blocking outbound | Check host/router firewall allows outbound HTTPS (443) and gRPC (8002) |
| Ansible `Permission denied` | SSH key correct? User has sudo? `ANSIBLE_BECOME_PASS` set for local mode? |

## Related Documentation

- [AWS Provider README](../AWS/README.md) — Central Hub and cloud Trust deployment
- [Kubernetes Provider README](../kubernetes/README.md) — K8s-based trust deployment via Helm
- [Trust README](../../../trust/README.md) — Trust service stack details
- [Deploy README](../../README.md) — General deployment prerequisites (AWS CLI, SSH keys, GHCR login)
