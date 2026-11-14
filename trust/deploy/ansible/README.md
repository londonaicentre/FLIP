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

# FLIP On-Premises Trust Provisioning (Ansible)

> **Deploys: trust only.** This playbook prepares a host the site already owns for the trust Compose stack in
> [`trust/deploy/`](../README.md); see [Prerequisites](#prerequisites) for what must already exist on the AWS
> side, and [where things live](../../../deploy/README.md#where-things-live) for the layout rule.

Ansible playbook and supporting files to provision an on-premises Ubuntu host as a FLIP Trust node. The provisioned host polls the Central Hub (running in AWS) for tasks — all communication is outbound from the trust.

`onprem.yml` is the on-prem counterpart of the EC2 plays [`aws-ec2-host.yml`](aws-ec2-host.yml), which
[`aws-ec2.yml`](aws-ec2.yml) runs against a node from [`../terraform/aws/ec2`](../terraform/aws/ec2/README.md). Together they implement the
[hybrid deployment model](../../../docs/source/deploy-flip.rst).

## Architecture

```text
         Internet
             │
             ▼
     ┌────────────────┐
     │     AWS CH     │
     │  (Central Hub) │
     └───▲───────▲────┘
         │       │
  polls  │       │  polls
 (HTTPS) │       │ (HTTPS)
         │       │
  ┌──────┴────┐ ┌┴──────────┐
  │ Trust A   │ │ Trust B   │
  │ (AWS EC2) │ │ (local)   │
  └───────────┘ └───────────┘

  Trusts poll the hub (outbound only)
```

Each local Trust host runs:

| Service | Container port | Protocol |
| --- | --- | --- |
| trust-api | 8000 | HTTP (polls hub outbound) |
| imaging-api | 8000 | HTTP (internal) |
| data-access-api | 8000 | HTTP (internal) |
| fl-client | — | TCP (connects outbound to FL server via NLB) |

**Host ports.** The on-prem kit file (`trust/.env.<CODE>.prod`) can set these host ports:

| Kit variable | On-prem kit | Dev (`make up`, first trust) |
| --- | --- | --- |
| `IMAGING_API_PORT` | `8005` | `8001` |
| `DATA_ACCESS_API_PORT` | `8014` | `8010` |
| `TRUST_API_PORT` | `8024` | `8020` |

The shifted ports let a prod-pointed trust run on a developer laptop next to `make up`. The dev ports are bound by `trust/deploy/compose/compose_trust-1_override.yml`. On a dedicated host you can set the kit values back to `8001` / `8010` / `8020` if your tooling expects them.

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

3. **AWS Central Hub deployed** — The Central Hub must be running in AWS (required for Terraform outputs and the FL participant kits in S3). See [`deploy/providers/AWS/`](../../../deploy/providers/AWS/README.md).

## Quick Start

All commands are run from the `deploy/providers/AWS/` directory since the Makefile targets there orchestrate both cloud and local infrastructure:

```bash
cd deploy/providers/AWS
```

### Running docker on the trust host

The playbook deliberately does **not** add the SSH login user to the `docker` group (docker
group membership is equivalent to root on the host — any member can mount `/`
into a container and chroot in), so docker commands and `make -C trust up-trust`
must be run via `sudo`:

```bash
cd ../../..
sudo -E make -C trust up-trust KIT=<CODE> HUB=<stag|prod>
```

Use the trust code you registered, whose kit file is `trust/.env.<CODE>.<stag|prod>`.
`-E` is load-bearing: it preserves `$HOME`, so root's docker client reuses your
`~/.docker/config.json` GHCR login for the image pulls — a plain `sudo` looks in
`/root/.docker` and the pulls fail. (`HUB` needs no preserving: it is a make argument.)
Any direct `docker`, `docker compose`, or
`docker swarm` invocations on the trust host should likewise be prefixed with `sudo`.

> **Re-running the playbook does not remove an existing docker-group grant.** Older
> versions of this playbook added the login user to the `docker` group, and
> `geerlingguy.docker`'s `docker_users` only ever adds members, never removes them.
> On a host provisioned by an older version, remove the user yourself, then have
> them log back in for it to take effect:
>
> ```bash
> sudo gpasswd -d <login-user> docker   # verify with: id -nG <login-user>
> ```

### Provision the trust host

Run this **on the trust host**: `provision-local-trust` runs Ansible against the machine it runs on.
To provision a remote host over SSH, use [Direct usage](#onpremyml) instead. The target lives in the
AWS provider Makefile, so run it from `deploy/providers/AWS/`:

```bash
# Set the sudo password (bash; fish: set -x ANSIBLE_BECOME_PASS (read -s -P 'Sudo password: '))
read -rsp 'Sudo password: ' ANSIBLE_BECOME_PASS && export ANSIBLE_BECOME_PASS

cd deploy/providers/AWS       # first time only, if you didn't `cd` earlier
make provision-local-trust KIT=<CODE> HUB=<stag|prod>
```

### What `provision-local-trust` does

1. Runs the Ansible playbook (`onprem.yml`), a composition of the shared roles in
   [`roles/`](roles/README.md) (the EC2 plays `aws-ec2-host.yml` compose the same ones):
   - Installs Docker and the base packages (`flip_base_packages`, `flip_docker` — no docker-group grant)
   - Creates the application, images and XNAT directories under `/opt/flip/` (`flip_trust_dirs`):
     - `/opt/flip/` and `/opt/flip/data/images/`, owned by `ubuntu`
     - the **per-net images bind sources** `/opt/flip/data/images/net-1`
       and `net-2`. Each fl-client mounts only its own net's slice, and both
       imaging-api and the fl-client *write* there, so these must exist and be
       writable before the first `up-trust`. `make up-trust` repairs their ownership
       itself (`ensure_net_dirs` in `trust/Makefile`, which runs under `sudo` on-prem
       and fails loudly if it cannot), so the trap is a `net-N` created root-owned by
       a path that bypasses make: then every image download 500s and training later
       fails with a misleading `num_samples=0`. Ownership is **backend-aware**, driven by
       the `fl_backend` extra-var the make target passes: FLARE's client shares
       imaging-api's uid, so `ubuntu:ubuntu` + `0755`; Flower's runs as `app`
       (uid/gid 49999) on upstream `flwr/base`, so group `49999` + `0775`. Re-run
       the playbook (or fix by hand) if you switch `FL_BACKEND`.
     - the **XNAT bind mounts** `/opt/flip/xnat`,
       `/opt/flip/xnat/xnat-data/{tomcat_logs,archive,build,cache,prearchive}` and
       `/opt/flip/xnat/xnat-db-data`, owned by **UID/GID 1001** — the in-image
       `xnat` user (`trust/xnat/xnat/Dockerfile`), not the login user. See the
       XNAT-directory warning below.
     - the **approved-cohort membership stores** (FLIP#857) `/opt/flip/cohort-snapshots/trust-1`
       and `trust-2`, owned by **UID/GID 1000** (data-access-api's in-image user) under a
       root-owned `/opt/flip/cohort-snapshots`. If data-access-api cannot write there, every
       project approval fails to freeze its cohort and the trust refuses that project's
       row-level data. `make up-trust` prepares any other slot's store itself.
   - Pre-creates the **FL participant kit tree** for the tarball you extract by hand (`flip_fl_kit` with
     `fl_kit_source: precreate`) under `/opt/flip/fl-kit` — the
     default `FL_KIT_DIR`, matching what the prod trust compose mounts:
     `${FL_KIT_DIR}/net-1/services/<slot>/{local,startup,transfer}` for FLARE
     and `${FL_KIT_DIR}/net-1/{certificates,keys}` for Flower. The slot
     sub-tree defaults to **`Trust_2`** (`fl_kit_precreate_slot` in the role's defaults).
     If the hub assigned this trust a different slot (check `FL_KIT_SLOT` in
     `trust/.env.<CODE>.<env>`), either pass `-e fl_kit_precreate_slot=<slot>` to the
     playbook, or just create the tree yourself afterwards:

     ```bash
     sudo install -d -o ubuntu -g ubuntu -m 0755 \
       /opt/flip/fl-kit/net-1/services/<slot>/{local,startup,transfer}
     ```

     An operator who overrides `FL_KIT_DIR` in their kit file owns the whole
     directory tree themselves — the playbook only ever provisions the default.
   - Copies the Loki/Alloy/Grafana config into `/opt/flip/config/observability`, where the production
     compose reads it (`flip_observability_config`)
2. Downloads the FL participant kit from S3 and stages it under `/tmp`, printing the `sudo rsync` commands to deploy it into `${FL_KIT_DIR}/net-1/...` (default `/opt/flip/fl-kit/net-1/...`).

> **Warning — the playbook's XNAT directory is not the one XNAT uses.** This playbook and
> `aws-ec2-host.yml` create `/opt/flip/xnat/...`, but XNAT writes to `XNAT_DATA_DIR` as resolved
> in `trust/xnat/Makefile`. Chowning `/opt/flip/xnat` looks right but changes nothing.
>
> - **Where it is:** the kit file's `XNAT_DATA_DIR` wins. Every scaffolded kit sets it:
>   `trust/.env.example` (copied by `make new-trust`) has `XNAT_DATA_DIR=./xnat-data`, i.e.
>   `<checkout>/trust/xnat/xnat-data`. If the kit omits it, the default is per-slot:
>   `/opt/flip/xnat-trust$(TRUST_NUM)` under `HUB=stag|prod` (`TRUST_NUM` = the kit's
>   `FL_KIT_SLOT_NUMBER`), `./xnat-data-trust<N>` in dev. This keeps two trusts on one host isolated.
> - **What to chown:** the kit's `XNAT_DATA_DIR` itself, to `1001:1001`. The live tree under it is
>   `xnat-data/{tomcat_logs,archive,build,cache,prearchive}` plus `xnat-db-data`. Compose, `xnat-reset` and the ownership check all
>   read the same variable. Set `XNAT_DATA_DIR` explicitly in the kit if you want one fixed path.
> - **Destructive:** `up-xnat` runs `xnat-reset`, which **deletes and recreates** `XNAT_DATA_DIR` with
>   the right owner, verifies it, and fails naming the exact `chown` if it is wrong. Do not use it to
>   repair a populated archive.

### Post-provisioning manual steps

1. **Start the trust stack** on the trust host, with `sudo -E` as explained in
   [Running docker on the trust host](#running-docker-on-the-trust-host):

   ```bash
   cd ../../..   # back to the repo root from deploy/providers/AWS
   sudo -E make -C trust up-trust KIT=<CODE> HUB=<stag|prod>   # the trust code you registered
   ```

2. **Verify** the trust can poll the hub (check trust-api logs for successful task polling — `sudo docker compose logs trust-api`).

## Trust Authentication

Any machine with the correct credentials can act as a trust: the hub identifies trusts by API key, not by IP address or hostname. The trust's kit file (`trust/.env.<CODE>.<env>`) must have:

| Variable | Purpose |
| --- | --- |
| `TRUST_API_KEY` | Per-trust secret key, written into the trust's kit file at registration (`make -C deploy/providers/AWS register-trusts KIT=<CODE> HUB=<hub>` on a deployed hub) |
| `CENTRAL_HUB_API_URL` | Hub URL the trust polls (e.g. `https://app.flip.aicentre.co.uk`) |
| `AES_KEY_BASE64` | Shared encryption key for trust-hub payloads |

**Hub-side prerequisites** (before the trust can connect):

1. The trust must be registered on the hub — run `make register-trusts`, or use the Add-Trust button on the Connection status page. Registration inserts the `trust` row and claims an FL kit slot; `make register-trusts` also sets its `api_key_hash`, while a trust added with the button has no keys until its kit is first downloaded from its card. No hub redeploy is needed.

`provision-local-trust` does not register the trust: it must already be registered.

## Ansible Playbook Details

### `onprem.yml`

The main playbook. It can be run standalone or via the `provision-local-trust` Makefile target. It
composes the roles in [`roles/`](roles/README.md); the play itself only states what an on-prem host does
differently from the EC2 trust (no docker-group grant, a pre-created kit tree instead of an S3 sync, one
images tree). Roles are found beside the play, so no `roles_path` configuration is needed.

**Optional variables:**

| Variable | Default | Description |
| --- | --- | --- |
| `flip_dir` | `/opt/flip` | Root application directory |
| `fl_backend` | `nvflare` | FL backend this trust will run. Sets the group/mode of the per-net images bind sources (`<flip_dir>/data/images/net-N`) — the Flower client runs as uid/gid 49999, not imaging-api's 1000, so it needs group 49999 + `0775` to write there. `provision-local-trust` passes the deployment's `FL_BACKEND` automatically. |
| `fl_kit_precreate_slot` | `Trust_2` | The FLARE slot whose `net-1/services/<slot>/{local,startup,transfer}` tree is pre-created; must match the kit file's `FL_KIT_SLOT`. |

**Data.** The playbook puts no data on the host. The OMOP and Orthanc stores start empty, and
`make -C trust up-trust` seeds them from the canonical dataset at bring-up (`ensure-seeded`).
The licensed core vocabulary is not seeded. On EC2 it is fetched from the hub's S3 bucket through an
instance role. On-prem, load it yourself with `make -C trust/omop-db load-omop-vocab`.

**Direct usage** (without the Makefile):

```bash
cd deploy/providers/AWS
uv run ansible-galaxy install -r ../../../trust/deploy/ansible/requirements.yml

uv run ansible-playbook \
  -i <trust-host-ip>, \
  -u ubuntu \
  --private-key ~/.ssh/trust_key \
  ../../../trust/deploy/ansible/onprem.yml
```

### `requirements.yml`

Ansible Galaxy dependencies:

- `geerlingguy.docker` — Docker installation role

Install it as shown in [Direct usage](#onpremyml) (from `deploy/providers/AWS/`, where `uv sync` installs Ansible); `provision-local-trust` does this for you.

## Network and Firewall Configuration

### Port Forwarding (NAT)

**No inbound port forwarding is needed.** Trusts poll the hub outbound for tasks, and FL clients connect outbound to the FL server through the networking account's edge NLB. All communication is trust-initiated; the hub never makes inbound requests to trusts.

The host/network firewall must allow outbound to **two separate Central Hub hosts** (different load balancers — both must be allowlisted):

- **`app.flip.aicentre.co.uk`** — the edge CloudFront (HTTPS API, port 443).
- **`fl.app.flip.aicentre.co.uk`** — the edge NLB (FL gRPC, default port 8002).

### Dynamic Public IP

The hub keeps no per-trust allowlist, so a changed public IP (common with residential broadband) needs no hub-side change.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Trust not polling hub | Trust stack running? (`sudo docker ps` on trust host). Check trust-api logs for polling errors. |
| `Connection timed out` (FL) or other outbound failures | Host/router firewall must allow outbound HTTPS (443) and gRPC (8002) to the two hosts in [Port Forwarding (NAT)](#port-forwarding-nat) |
| Ansible `Permission denied` | SSH key correct? User has sudo? `ANSIBLE_BECOME_PASS` set for local mode? |

## Related Documentation

- [AWS Provider README](../../../deploy/providers/AWS/README.md) — Central Hub and cloud Trust deployment
- [Helm chart README](../helm/README.md) — the same trust stack rendered for Kubernetes
- [Trust Compose stack README](../README.md) — the compose files
- [Trust README](../../README.md) — Trust service stack details
- [Deploy README](../../../deploy/README.md) — General deployment prerequisites (AWS CLI, SSH keys, GHCR login)
