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

# Pre-configurations needed for FLIP Deployment

## Supported PostgreSQL Versions

FLIP uses AWS RDS PostgreSQL with the following version support policy:

- **Current Version**: PostgreSQL 17 (EOL: November 2029) ✓
- **Minimum Version**: PostgreSQL 15
- **Deprecated**: PostgreSQL 13 (EOL: November 2025) ❌ EXPIRED

**Version Lifecycle:**

| Version | EOL | Status |
| ------- | --- | ------ |
| PostgreSQL 13 | November 2025 | ❌ EXPIRED — do not use |
| PostgreSQL 14 | October 2026 | ❌ EXPIRED — do not use |
| PostgreSQL 15 | October 2027 | ⚠️ Deprecating soon |
| PostgreSQL 16 | October 2028 | ✓ Supported |
| PostgreSQL 17 | November 2029 | ✓ Current (Terraform default) |

**Upgrade Policy**: Plan PostgreSQL major version upgrades with a 6-month lead time before EOL. AWS charges premium rates for EOL versions. To change the version, update the `postgres_version` variable in `deploy/providers/AWS/variables.tf`.

## Deployment Architecture

### Prerequisites

#### Step 1: Authenticate with GitHub Container Registry

Log in to GHCR to pull pre-built images from CI/CD:

Create a token with `read:packages` scope from GitHub settings.
We recommend following [GitHub's guide](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry#authenticating-with-a-personal-access-token-classic).

```bash
echo <GITHUB_PAT> | docker login ghcr.io -u <GITHUB_USERNAME> --password-stdin
```

> **Note**: You need a GitHub Personal Access Token (PAT) with `read:packages` permission.

#### Step 2: Configure AWS CLI SSO

Set up AWS CLI with SSO for authentication:

```bash
aws configure sso
```

if you have already configured SSO, you can then login with:

```bash
aws sso login
```

#### Step 3: Get SSH key configured

Generate an SSH key pair for EC2 instance access:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/host-aws -C "YOUR_EMAIL@example.com"
```

This key will automatically be uploaded to AWS during deployment and can be found in the AWS console under AWS EC2 > Network & Security > Key Pairs.

See `TF_VAR_flip_keypair` and `TF_VAR_ec2_public_key_path` in the Terraform environment configuration if you need to customize the key name or path.

### Final configuration

#### Verify AWS SES email address

The SES email address will have received a verification link you need to click. Then, to check the email has been verified, log in to the AWS console, navigate to the SES service, and check the Configuration > Identities section.

#### Cognito Email Configuration

FLIP uses AWS Cognito for user authentication and includes branded email templates for temporary password invitations and password reset flows. The email templates are deployed as part of the Terraform infrastructure.

**Email Templates:**

1. **Temporary Password Email** (`admin_create_user_config.invite_message_template`)
   - Sent when administrators invite users to FLIP
   - Contains: Username and temporary password
   - Uses Cognito placeholders:
     - `{username}` — User's Cognito username
     - `{####}` — 6-character temporary password

2. **Password Reset Email** (`verification_message_template`)
   - Sent when users request password reset
   - Includes both verification code and reset link options
   - Uses Cognito placeholders:
     - `{####}` — 6-character verification code
     - `{##...##}` — Dynamically generated password reset link token

**SES Email Verification Requirement:**

Before deploying Cognito email templates, the SES email address must be verified:

```bash
cd deploy/providers/AWS

# For initial deployment or if verification has expired:
# 1. Delete the existing SES identity in AWS Console (if expired):
#    - Navigate to AWS SES > Configuration > Identities
#    - Delete the FLIP email identity
# 2. Re-verify the email address
#    - Run: make plan apply
#    - Check your email inbox for AWS SES verification link
#    - Click the link to confirm the email address
# 3. Verify the configuration:
#    - Return to AWS SES > Identities
#    - Confirm the email status shows "Verified"
```

**Testing Email Delivery:**

After deployment, test the email configuration by creating a test user:

```bash
# Get the Cognito user pool ID
USER_POOL_ID=$(cd deploy/providers/AWS && terraform output -raw cognito_user_pool_id)

# Create test user (suppress automatic email)
aws cognito-idp admin-create-user \
  --user-pool-id "$USER_POOL_ID" \
  --username testuser@example.com \
  --message-action SUPPRESS

# Verify the temporary password email is received with correct formatting
# (Check inbox for email from FLIP with temporary credentials)

# Test password reset flow
# 1. Login as testuser with temporary password
# 2. Change password to permanent one
# 3. Logout and request password reset
# 4. Verify password reset email arrives with verification code or reset link

# Verify email rendering across clients:
# - Gmail
# - Outlook
# - Apple Mail
# - Mobile email clients
```

**Manual Verification Checklist:**

- [ ] SES email identity shows "Verified" status in AWS Console
- [ ] Temporary password email includes username and temporary password
- [ ] Password reset email includes 6-digit verification code or reset link
- [ ] Email templates render correctly in Gmail, Outlook, Apple Mail
- [ ] Links in email templates resolve to correct environment subdomain (e.g., `https://flip-staging.example.com`)
- [ ] SMS fallback messages deliver (if SMS is enabled in Cognito)

#### Cognito MFA Administration

FLIP enforces TOTP (Time-based One-Time Password) MFA for every Cognito user. The enforcement lives in the **application layer**, not in the Cognito pool configuration — the pool's `mfa_configuration` is deliberately set to `OPTIONAL`. This section explains why, how administrators reset MFA for other users, and how an administrator who has lost their own authenticator can recover via the AWS CLI.

##### Why app-layer MFA (not pool `mfa_configuration = "ON"`)

Cognito exposes no admin API to delete a user's verified TOTP secret. With the pool set to `ON`, calling `AdminSetUserMFAPreference` with `Enabled=False` leaves the old secret registered in Cognito — at the next sign-in, Cognito still issues a `SOFTWARE_TOKEN_MFA` challenge and asks the user for a code their (lost) authenticator can no longer generate. The account becomes permanently locked out short of deleting and recreating the user.

With `mfa_configuration = "OPTIONAL"`, disabling the preference actually takes effect: Cognito signs the user in without a challenge. The application layer then catches the user — `flip-api` `verify_token` checks whether `SOFTWARE_TOKEN_MFA` is present in the user's `UserMFASettingList` and returns 403 if not, and the `flip-ui` router guard routes the user to the post-auth enrolment page where they mint a fresh TOTP secret. First-time users follow the same path: they sign in with their temporary password, the app sees `mfaEnabled=false`, and they are walked through enrolment before any protected route is reachable.

SMS MFA is intentionally disabled — it would introduce an SNS dependency and reintroduce SIM-swap risk. The rationale is documented inline at `deploy/providers/AWS/modules/cognito/main.tf` (around the `mfa_configuration` line) and the enforcement point lives in `flip-api/src/flip_api/auth/dependencies.py` (`verify_token`).

##### `ENFORCE_MFA` flag

The MFA gate is controlled by `flip-api`'s `ENFORCE_MFA` setting. The Settings default is `true` — when active, `verify_token` returns 403 for any signed-in user without an active TOTP and the UI's router guard redirects them through enrolment.

The dev override lives in `deploy/compose.development.yml` (`ENFORCE_MFA=false`) so local development doesn't force enrolment on a burner authenticator. **The flag is intentionally not exposed in `.env.development.example` or AWS Secrets Manager** — the Settings default (`true`) is the canonical secure anchor. `deploy/compose.production.yml` passes `ENFORCE_MFA=${ENFORCE_MFA:-true}` so operators can override it from `.env.stag`/`.env.production` for testing (e.g. `ENFORCE_MFA=false`), but it falls back to the secure `true` default when unset — do not commit an override into either env file for a real deployment.

The flag is mirrored to the UI via `/users/me/mfa/status` (`required: bool`) so the router guard knows when to skip the enrolment redirect.

##### Resetting MFA for another user

For users other than yourself, use the FLIP Admin UI. See the *Reset User MFA* subsection in [`docs/source/sys-admin/admin-project-and-user-management.rst`](../docs/source/sys-admin/admin-project-and-user-management.rst) for the step-by-step flow. The UI calls `POST /users/{user_id}/mfa/reset` on `flip-api`, which runs the same two Cognito operations documented below but under the FLIP permission model (requires `CAN_MANAGE_USERS`) and leaves an application-level audit trail.

##### Recovering an administrator account that has lost its authenticator

This runbook is for the case where **you** have lost access to your TOTP device and the UI flow above is therefore unavailable (you cannot sign in to reach the Admin Area). Another operator with AWS credentials runs these commands on your behalf. Direct AWS CLI access is required:

**Prerequisites:**

- AWS credentials for the account that hosts the Cognito user pool (the same SSO profile used for `make full-deploy`)
- IAM permissions for `cognito-idp:AdminSetUserMFAPreference` and `cognito-idp:AdminUserGlobalSignOut`

**Steps:**

1. Fetch the Cognito user pool ID from Terraform (or read it from the AWS Console):

   ```bash
   # From the stack the admin belongs to — prod/stag root or dev root
   cd deploy/providers/AWS         # or deploy/providers/AWS/dev
   tofu output -raw cognito_user_pool_id
   ```

2. Clear the locked-out administrator's TOTP preference:

   ```bash
   aws cognito-idp admin-set-user-mfa-preference \
     --user-pool-id "$USER_POOL_ID" \
     --username admin@example.com \
     --software-token-mfa-settings Enabled=false,PreferredMfa=false
   ```

3. Revoke the administrator's existing refresh tokens so no pre-reset session can keep operating:

   ```bash
   aws cognito-idp admin-user-global-sign-out \
     --user-pool-id "$USER_POOL_ID" \
     --username admin@example.com
   ```

4. The administrator now signs in with their existing password. Because `SOFTWARE_TOKEN_MFA` is no longer in their `UserMFASettingList`, the `flip-api` MFA gate and the `flip-ui` router guard funnel them through the post-auth enrolment page where they register a new authenticator. Their password does not need to be reset.

> **Note:** These two CLI commands have exactly the same server-side effect as clicking **Reset MFA** in the Admin UI — the UI endpoint (`reset_user_mfa` in `flip-api/src/flip_api/utils/cognito_helpers.py`) calls `admin_set_user_mfa_preference` followed by `admin_user_global_sign_out`. The CLI path exists only because it does not require a signed-in FLIP session.
>
> **Warning:** This path is an AWS-level escape hatch and is **not** audit-logged inside FLIP. Use it only for administrator self-recovery. For any user who is not currently locked out of FLIP itself, prefer the Admin UI flow so the reset is captured in the application logs.

## Deployment Models

### Central Hub

The Central Hub has **one supported production deployment**: ECS Fargate via the Terraform root in
[`deploy/providers/AWS/`](providers/AWS/README.md). The task definitions in `ecs_tasks.tf` (env maps in
`locals.tf`) are the **canonical definition of production container config**. Deploying into an AWS
LZA-governed account is an env-gated **mode** of that same root, not a separate path
([FLIP#749](https://github.com/londonaicentre/FLIP/issues/749)). The ECS FL task definitions serve **both
FL backends** ([FLIP#566](https://github.com/londonaicentre/FLIP/issues/566)): `FL_BACKEND` in the env file
switches the same task families between NVFLARE and Flower (SuperLink ports/command/creds — Flower
additionally needs `FLOWER_KIT_DATE` and provisioned creds uploaded via
`make -C fl-services/flower provision upload-creds-to-s3`, with `FLOWER_EXTRA_SERVER_SANS` covering the
Cloud Map + public FL hostnames).

> **Deprecated — hub on EC2/compose** ([FLIP#936](https://github.com/londonaicentre/FLIP/issues/936)):
> running the hub via `compose.production*.yml` on an EC2 or self-managed host is no longer a supported
> deployment target (the former hub EC2 host was long since replaced by a minimal SSM bastion). The
> `compose.production*.yml` files remain maintained **only** as the local prod-image harness
> (`make up PROD=stag|true` — the baked images, no dev mounts). When changing production config, change
> Terraform first and update the compose files only as far as the local harness needs. Remaining hub-EC2
> material is removed once the LZA migration's legacy decommission lands (FLIP#749 WP6).

### Trusts

FLIP supports three trust deployment models:

| Model | Location | Documentation |
| ------- | ---------- | --------------- |
| **Cloud (EC2)** | AWS EC2 (same account as Central Hub) | [`deploy/providers/AWS/README.md`](providers/AWS/README.md) |
| **Hybrid / On-Premises** | Any Ubuntu host (home lab, hospital server) | [`deploy/providers/local/README.md`](providers/local/README.md) |
| **Kubernetes** | Any K8s cluster 1.28+ (EKS, AKS, on-prem) | [`deploy/providers/kubernetes/README.md`](providers/kubernetes/README.md) |

In all models, trusts poll the Central Hub for tasks over HTTPS — all communication is **outbound** from the trust. The hub never makes inbound requests to trusts.

## Container Security Hardening

All Docker containers in FLIP are hardened with the following measures:

### Non-Root Users

Each Dockerfile explicitly drops root privileges by running the application as a non-root user:

| Service | User | Notes |
|---------|------|-------|
| flip-api | `app` (UID 49999) | Set in the flip-api Dockerfile |
| flip-ui | `root` | Intentionally runs as root in dev (Vite dev server with bind-mounted source); not present in production where the UI ships as static files served by CloudFront. |
| orthanc | `orthanc` (UID 999) | Set via `USER orthanc` in the orthanc Dockerfile; the user pre-exists in the `orthancteam/orthanc` base image |
| xnat-web | `xnat` | Created in the XNAT Dockerfile (UID 1001) |
| xnat-nginx | `nginx` | Pre-existing in the base image (`nginx`) |
| xnat-db | `postgres` | Pre-existing in the base image (`postgres`) |
| xnat-socket-proxy | `root` | Upstream `tecnativa/docker-socket-proxy` image — HAProxy connects to the root-owned Docker socket as its owner. Runs under `cap_drop: ALL` with no capabilities added back. |
| flip-db / omop-db | `postgres` | Pre-existing in the base image (`postgres`) |

**Bind-mount ownership.** Because XNAT (`xnat`, UID 1001) and Orthanc (`orthanc`, UID 999) no
longer run as root, the host-side bind-mount source directories must be owned by the matching
UID. The Ansible playbooks `deploy/providers/AWS/site.yml` and
`deploy/providers/local/site_local_trust.yml` provision `/opt/flip/xnat/**` as UID 1001 and
`/opt/flip/orthanc/**` as UID 999 — including a recursive `chown` after extracting the Orthanc
storage archive (which `tar` writes as root). If you provision a trust host outside Ansible, you
must replicate this ownership or first-boot writes (archive ingest, SQLite index, log rotation)
will fail with EACCES. In dev, `trust/orthanc/update_orthanc_data.sh` instead `chmod`s the mock
storage world-writable so a developer needs no `sudo` to re-seed it.

### Linux Capability Restrictions

Every container drops **all** Linux capabilities (`cap_drop: [ALL]`) and only adds back what the
service strictly requires. Two dev-only services (pgadmin, register-supernode-keys) are
deliberately exempted because their entrypoints depend on root capabilities that would crash-loop
under `cap_drop: ALL`. The standalone `fl-services/nvflare/compose.dev.yml` dev harness's
`fl-client-1`/`fl-client-2` — used only by `make -C fl-services/nvflare up` for iterating on the
backend outside the full trust stack — carry no `cap_drop` either, but for a different reason:
they are simply **not hardened yet**, not blocked from it. They run the same non-root
`flare-fl-client` image and the same kit dirs as the hardened `fl-client-net-*` services below, so
a later pass can harden them the same way. The trust-deployment `fl-client-net-*` services (in
`trust/deploy/compose_trust.*.yml`, what a real trust actually runs) are hardened — see the rows
below. The per-service grants in the compose files are:

| Service(s) | Granted capabilities | Reason |
|------------|----------------------|--------|
| flip-api, fl-api (Flower), trust-api, imaging-api, data-access-api, xnat-web, loki, alloy, grafana | `CHOWN` | In-container init/entrypoint fixes ownership on volume paths it owns. |
| fl-client-net-* (Flower — production and development; NVFLARE development) | *(none)* | Runs non-root (GHSA-8465), and Docker grants effective capabilities only to root — a `cap_add` here would land in the bounding set with `CapEff` still `0`, so it would buy nothing. Flower's `flower-supernode` entrypoint does no chmod/chown at all and its dev mounts are `:ro`. NVFLARE's dev kit (`provision/workspace-dev/`) is written by `make provision` as the host user whose UID is baked into the `:dev` image, so ownership already matches; when it doesn't (CI, a shared devbox, a teammate's prebuilt image) the fix is to `chown` the kit dirs to the container UID, not to grant a capability. |
| fl-client-net-* (NVFLARE, production) | `DAC_OVERRIDE`, `FOWNER` (production) | Inert for the current image, which runs non-root from PID 1 — the Ansible-provisioned `FL_KIT_DIR` is pre-chowned to the container's UID by `site.yml` / `site_local_trust.yml`. Kept for legacy root-image compat: trusts pin `DOCKER_FL_TAG` (an immutable `sha` tag is the documented norm), so `--pull always` cannot move a trust off a pre-GHSA-8465 **root** image, and under `cap_drop: ALL` such an image loses root's implicit DAC bypass on the `envsubst` write into the bind-mounted `local/` and `FOWNER` on the `chmod +x` of `startup/*.sh`. Its writes predate the `\|\| exit 1` guard, so it degrades to running NVFLARE against a stale/absent `resources.json` rather than crash-looping — a worse failure to diagnose. Same rationale as the orthanc row below. |
| fl-api (NVFLARE) | `CHOWN` | The `flare-fl-api` image runs as user `flip` (UID 1001, non-root), so only the `CHOWN` baseline is needed; `DAC_OVERRIDE` and `FOWNER` are inert for non-root processes. |
| fl-server (NVFLARE) | `CHOWN`, `DAC_OVERRIDE`, `FOWNER` | The container runs as root, but the provisioned NVFLARE kits are bind-mounted owned by the provisioning uid with 0600 keys. `cap_drop: ALL` strips root's implicit DAC bypass, so without `DAC_OVERRIDE` the fl-server crash-loops on `/app/startup/server.key`; the entrypoint also `chmod`s kit scripts it does not own (`FOWNER`). In dev, the same grant lets the root fl-server read the operator's 0600 AWS SSO token cache for the S3 results upload. |
| fl-server (Flower, development only) | `CHOWN`, `DAC_OVERRIDE` | The dev compose runs the SuperLink as root (see the `user: "0:0"` comment in `compose.development.flower.yml`) to read the host-provisioned 0640 TLS keys and the operator's 0600 SSO token cache; `cap_drop: ALL` strips root's implicit DAC bypass, so `DAC_OVERRIDE` is granted back. Production runs the image's non-root user with instance-role AWS credentials and keeps the `CHOWN` baseline. |
| flip-db, omop-db, xnat-db | `CHOWN`, `DAC_OVERRIDE`, `FOWNER`, `SETUID`, `SETGID` | The official postgres entrypoint runs as root and `gosu`-drops to the `postgres` user (`SETUID`/`SETGID`). On every start it re-runs `chmod 00700 $PGDATA` and a `chown` sweep over the persisted data dir, which on subsequent boots is already owned by `postgres` with mode `0700` — `chmod` on a dir owned by another uid needs `FOWNER` and traversing it needs `DAC_OVERRIDE`. Without them the container exits 1 on a persisted volume (see commit history, FLIP#485). |
| orthanc | `CHOWN`, `DAC_OVERRIDE`, `FOWNER` (production) | Runs non-root (UID 999) from PID 1, so `DAC_OVERRIDE`/`FOWNER` are inert for the current image — they're kept for legacy root-image compat, since a pre-hardening orthanc image runs its entrypoint as root and crash-loops under `cap_drop: ALL` without them (hostid write, plugin symlink fixup). The storage bind mount is made 999-writable at the provisioning layer rather than fixed up with in-container caps; `CHOWN` is kept as the shared baseline. |
| xnat-nginx | `CHOWN`, `NET_BIND_SERVICE` | `NET_BIND_SERVICE` lets the non-root `nginx` user bind port 80 (Docker sets `net.ipv4.ip_unprivileged_port_start=0`, but capabilities are still checked). |
| xnat-socket-proxy | *(none)* | HAProxy runs as root, which owns the mounted Docker socket, so it connects with an empty capability set — nothing needs adding back on top of `cap_drop: ALL`. See [Docker Socket Isolation](#docker-socket-isolation-xnat-container-service). |
| flip-ui (development only) | `CHOWN`, `DAC_OVERRIDE`, `SETUID`, `SETGID`, `NET_BIND_SERVICE` | Vite dev server with bind-mounted source; not present in production where the UI ships as static files served by CloudFront. |

Notably **not** granted anywhere: `SYS_ADMIN`, `SYS_PTRACE`, `SYS_MODULE`, `MAC_*`, `AUDIT_*`,
`DAC_READ_SEARCH`. Combined with `no-new-privileges` (below), this defeats the standard
`setuid`-binary and `LD_PRELOAD` escalation paths even if an attacker achieves RCE inside a
container.

> **Hardened images and hardened compose must ship together.** The minimal-caps compose files
> assume the rebuilt **non-root** `orthanc` and `xnat-web` images. Until CI publishes those (on
> merge to `develop`/`main`), the previously published root-running GHCR images break under the
> new caps: orthanc crash-loops writing its `orthanc`-owned `/etc/hostid` (root without
> `DAC_OVERRIDE`), and xnat-web — still root — cannot write its UID-1001 bind mounts
> (config/archive/build/cache/plugins), so `make up`'s XNAT configure step fails on both trusts
> (swarm enforces `cap_drop` even though it ignores `security_opt`). When running this hardening
> from a branch whose images are not yet published, build the hardened images locally:
> `make up BUILD=true` covers the compose-built services (including orthanc), and the XNAT stack
> needs `make -C trust/xnat build DOCKER_REGISTRY= DOCKER_TAG=dev` followed by redeploying the
> stack with the same `DOCKER_REGISTRY=`/`XNAT_TAG=dev` overrides so `docker stack deploy`
> resolves the locally built image instead of the GHCR one.

### Docker Socket Isolation (XNAT Container Service)

XNAT's Container Service plugin launches processing containers — currently `xnat/dcm2niix`,
which every project created with DICOM→NIfTI conversion enabled triggers automatically on scan
archive. It used to do this through `/var/run/docker.sock` mounted straight into `xnat-web`,
which is a root-equivalent capability: anything that compromises XNAT can exec into any container
on the host, start privileged containers, or mount arbitrary host paths.

`xnat-web` no longer mounts the socket. The XNAT stack instead runs a
[`tecnativa/docker-socket-proxy`](https://github.com/Tecnativa/docker-socket-proxy) sidecar
(`xnat-socket-proxy` in `trust/xnat/docker-compose-stack.yml`) that holds the socket read-only
and serves a filtered Docker API (`tcp://xnat-socket-proxy:2375`) on a dedicated stack-scoped
overlay network (`<stack>_socket-proxy`, `internal: true`, no published host port). Only
`xnat-web` is dual-homed onto that network — the other containers on the shared trust overlay
(`trust-api`, `imaging-api`, `data-access-api`, `orthanc`, the fl-client) have no route to the
proxy at all. The Container Service is pointed at that endpoint by
`trust/xnat/xnat/config/container-service-backend-configuration.json`, which
`configure-dcm2niix.sh` POSTs to `/xapi/docker/server` — and the configure run now hard-fails if
`/xapi/docker/server/ping` cannot reach Docker through the proxy, instead of leaving a
registered-but-unlaunchable dcm2niix command behind.

The proxy allowlists only what a swarm-mode Container Service launch needs — `SERVICES`
(+ `POST` for the mutating calls), `TASKS`, `NODES`, `IMAGES` (pull-on-init), `INFO`, `SWARM`
(the plugin's swarm-mode connection test is a `GET /swarm` inspect), plus the proxy's default
`PING`/`VERSION`/`EVENTS`. Everything else is refused with a 403: `exec`, the container API,
volumes, networks, secrets, configs, plugins, and build.

**Multiple trusts on one host.** Each trust's XNAT is a separate swarm stack (`xnat<N>`), so each
gets its **own** proxy on its **own** `xnat<N>_socket-proxy` network — they are not shared, which
keeps the trust boundary intact. The identical `xnat-socket-proxy` service name in both stacks
does not collide: swarm resolves it per-network, so each `xnat-web` reaches only its own proxy
(cross-stack access is refused), and the proxy publishes no host port. Both proxies holding the
same host socket is fine — it serves concurrent clients, exactly as it did when each `xnat-web`
mounted it directly.

This is a choke point, not a sandbox. Two residual powers are worth stating plainly: the services
API the Container Service depends on can itself create a service with an arbitrary host bind
mount, and because the proxy's `POST` switch is global, granting `SWARM` also exposes
`POST /swarm/*`. So a fully compromised `xnat-web` is not contained — but it loses direct
container/exec access entirely, and every remaining call now crosses one auditable gateway
(`docker service logs <stack>_xnat-socket-proxy` shows the full request log) instead of speaking
to the raw socket.

### No New Privileges

Nearly every container declares `security_opt: [no-new-privileges:true]` to prevent privilege escalation
via `setuid` binaries or `LD_PRELOAD` injection. The same dev-only services exempted from `cap_drop`
(pgadmin, register-supernode-keys, fl-clients) also omit `no-new-privileges`, and the XNAT swarm
stack ignores the `security_opt` key (see caveat below).

> **XNAT swarm caveat.** The XNAT stack (`xnat-web`, `xnat-db`, `xnat-nginx`, `xnat-socket-proxy`)
> is deployed with
> `docker stack deploy` (see `trust/xnat/Makefile`), and swarm **ignores** the `security_opt` key —
> so `no-new-privileges` is *not* applied to those four services from the compose file. The
> `cap_drop`/`cap_add` hardening still takes effect (swarm honours those). To enforce
> no-new-privileges on XNAT hosts, set it as the Docker daemon default in `/etc/docker/daemon.json`:
> `{ "no-new-privileges": true }`. Verify with the `docker inspect` command below.

### Verification

To verify hardening on a running container:

```bash
# Check the running user (should NOT be root)
docker exec <container> whoami

# Check effective capabilities
docker exec <container> cat /proc/1/status | grep CapEff

# Confirm no-new-privileges is active
docker inspect <container> --format '{{.HostConfig.SecurityOpt}}'
```

## Service Authentication

FLIP uses three separate authentication mechanisms for service-to-service communication. The single
hub-internal key is generated standalone; the two per-trust keys are minted together when you register each
trust:

```bash
make generate-internal-service-key   # fl-server → flip-api (single hub-internal secret)
make register-trusts                 # mints each trust's TRUST_API_KEY + TRUST_INTERNAL_SERVICE_KEY into its kit file
```

### Trust API Keys (trust-api → flip-api)

Each trust has a single `TRUST_API_KEY` (plaintext) minted by `register_trust` (`make register-trusts`) and held
only in that trust's kit file (`trust/.env.<CODE>.<env>`), sent in the `TRUST_API_KEY_HEADER` header. The hub stores
only the SHA-256 hash, in the `trust` table's `api_key_hash` column — there is no hub-side env dict of trust keys.
Used for task polling, cohort result submission, and heartbeat endpoints.

### Internal Service Key (fl-server → flip-api)

The fl-server on the Central Hub authenticates to flip-api using `INTERNAL_SERVICE_KEY` (a plain string) sent
in the `INTERNAL_SERVICE_KEY_HEADER` header. The hub validates it against `INTERNAL_SERVICE_KEY_HASH` (the
SHA-256 hash of the key). Used for model status updates, training metrics, and training log endpoints. This
is a **single, hub-internal** secret and is separate from the per-trust internal keys below.

### Trust-Internal Service Keys (trust-api / imaging-api / fl-client → imaging-api / data-access-api)

Inside each trust, every call from trust-api / imaging-api / fl-client to imaging-api or data-access-api
carries a shared-secret header. The header name comes from `TRUST_INTERNAL_SERVICE_KEY_HEADER` (default
`X-Trust-Internal-Service-Key`); the value is the per-trust plaintext `TRUST_INTERNAL_SERVICE_KEY`, minted by
`register_trust` into the trust's kit file (`trust/.env.<CODE>.<env>`) alongside `TRUST_API_KEY`. Receivers compare
the header against their own copy with a constant-time compare. `/health` is intentionally exempt so liveness
probes still work.

Each trust gets a distinct key — a leak in `Trust_1` cannot drive operations on `Trust_2`'s APIs. The hub
never sees these keys: they live only in trust-side env (the trust's kit file `trust/.env.<CODE>.<env>`, which
`trust/Makefile` `-include`s so every trust-internal container inherits it). See the **Trust-internal Service
Authentication** section in the repo-root [`CLAUDE.md`](../CLAUDE.md) for the full threat model.

FL clients (trust side) **do not** have Central Hub API credentials. Only the fl-server communicates with flip-api.
FL clients relay metrics and exceptions to the fl-server, which forwards them to the Central Hub.

The fl-server must reach flip-api via `FLIP_API_INTERNAL_URL` — a Docker-network URL such as
`http://flip-api:8000/api`. It must **not** go through the public `CENTRAL_HUB_API_URL` because the
CloudFront distribution in front of flip-api whitelists only `Authorization`, `Content-Type`, and
`Origin` and strips `X-Internal-Service-Key` at the edge, which would break this handshake. The
public `CENTRAL_HUB_API_URL` is reserved for flip-ui and trust-side (trust-api) consumers that live
outside the hub's Docker network.

| Variable | Where used | Purpose |
| --- | --- | --- |
| `TRUST_API_KEY_HEADER` | flip-api, trust-api | Header name for trust auth |
| `TRUST_API_KEY` | trust-side kit file | Per-trust plaintext key (hub stores its SHA-256 in the `trust` table's `api_key_hash`) |
| `INTERNAL_SERVICE_KEY_HEADER` | flip-api, fl-server | Header name for internal service auth |
| `INTERNAL_SERVICE_KEY` | fl-server | Internal service plaintext key (plain string) |
| `INTERNAL_SERVICE_KEY_HASH` | flip-api | SHA-256 hash of internal service key (plain string) |
| `TRUST_INTERNAL_SERVICE_KEY_HEADER` | trust-api, imaging-api, data-access-api, fl-client | Header name for trust-internal service auth (default `X-Trust-Internal-Service-Key`) |
| `TRUST_INTERNAL_SERVICE_KEY` | trust-side kit file | Per-trust plaintext key, minted by `register_trust`. The hub never sees it. |
| `CENTRAL_HUB_API_URL` | flip-ui, trust-api | Public base URL of flip-api (in prod: CloudFront URL) |
| `FLIP_API_INTERNAL_URL` | fl-server | Docker-network URL of flip-api on the Central Hub (e.g. `http://flip-api:8000/api`) |

#### Note on ECS networking

The Central Hub runs on ECS Fargate (see [Deployment Models](#deployment-models)).
`FLIP_API_INTERNAL_URL` names the intent ("flip-api's internal URL on the Central Hub"), not the
mechanism: on ECS, Terraform sets it to the Cloud Map private-DNS name
(`http://flip-api.flip.local:8000/api`, built in `locals.tf`); on the local compose harness it is the
Docker-network URL (`http://flip-api:8000/api`).

What it must not be: the public CloudFront URL. That's orthogonal to compute — CloudFront strips
`X-Internal-Service-Key` at the edge. Internal ALBs preserve all request headers by default, so an
internal-ALB value would also work; CloudFront doesn't.

> **Note on troubleshooting**: AWS-deployment-specific failure modes (Terraform state drift, ECS
> service stuck in `PENDING`, CloudFront cache invalidation, RDS connectivity) are documented in
> [`providers/AWS/TROUBLESHOOTING.md`](providers/AWS/TROUBLESHOOTING.md).
