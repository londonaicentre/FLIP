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

> [!NOTE]
> **This README is maintainer notes** for the deployment layer: where things live, the hub's production
> container config, container hardening and the service-authentication design. **To deploy or operate FLIP, use the
> operator manual on ReadTheDocs:** [Deploy the Central Hub](https://londonaicentreflip.readthedocs.io/en/latest/deploy-flip/deploy-central-hub.html) (prerequisites, registering trusts, deploys,
> email, MFA recovery), [on AWS (LZA)](https://londonaicentreflip.readthedocs.io/en/latest/deploy-flip/deploy-central-hub-aws-lza.html), [a trust on-prem](https://londonaicentreflip.readthedocs.io/en/latest/deploy-flip/deploy-flip-node-on-prem.html) and [a trust on Kubernetes](https://londonaicentreflip.readthedocs.io/en/latest/deploy-flip/deploy-flip-node-kubernetes.html).

## Where things live

Deployment artifacts are **not** all under `deploy/`. Two different concerns share the word "deploy", and
they are filed by two different rules:

1. **Application composition** — which containers form a stack. A compose file lives **next to the source
   tree whose images it builds**, so most of them are outside this directory.
2. **Infrastructure provisioning** — accounts, hosts, clusters. That is `deploy/providers/`.

| I want to… | Go to |
| ---------- | ----- |
| Run/change the **Central Hub** container stack | `deploy/compose.*.yml` |
| Run/change the **trust** container stack | [`trust/deploy/`](../trust/deploy/README.md) |
| Run/change the **XNAT** swarm stack | `trust/xnat/docker-compose-stack*.yml` |
| Build/populate the mock **OMOP** database | `trust/omop-db/compose.yml` |
| Run the **FL** services standalone | `fl-services/<backend>/compose*.yml` |
| Provision **AWS** (hub ECS + optional cloud trust EC2) | [`deploy/providers/AWS/`](providers/AWS/README.md) |
| Provision an **on-prem** trust host (Ansible) | [`trust/deploy/ansible/`](../trust/deploy/ansible/README.md) |
| Deploy a trust to **Kubernetes** (Helm) | [Deploy a trust on Kubernetes](https://londonaicentreflip.readthedocs.io/en/latest/deploy-flip/deploy-flip-node-kubernetes.html) (chart: [`trust/deploy/helm/`](../trust/deploy/helm/README.md)) |

Every compose file in **this** directory is Central-Hub-only — `flip-ui`, `flip-api`, `flip-db`, `pgadmin`,
`keycloak` (development only: the local identity provider, ephemeral — no volume — importing
[`keycloak/flip-realm.json`](keycloak/README.md) at every boot with `start-dev --import-realm`; a realm edit
needs `make reset-keycloak`, since the import skips a realm that already exists), `object-store` (development
only, FLIP#1291: an S3-compatible RustFS container standing in for the S3 buckets, its data the host-owned
`./object-store/` where a top-level directory is a bucket — `make up` pre-creates one per bucket name, the
`jobs/` idiom; every service reaches it through `AWS_ENDPOINT_URL_S3`, the fl-apis admit it as
`BUNDLE_URL_ALLOWED_ORIGINS=http://object-store:9000`, and `make clean-object-store` empties it), and the
`fl-api-net-*` / `fl-server-net-*` FL server side. Nothing in the development stack mounts `~/.aws` or
reaches an AWS service: sign-in, email and object storage are all local (FLIP#919, FLIP#1291). No trust service is defined here. The one place
hub compose touches "trust" is **networking**: `compose.development.yml` joins
`central-hub-trust-apis-network`, `trust-network-1/2` and `fl-net-1/2` as `external: true` — exactly as
the trust composes do. Neither side *creates* them; `make create-networks` does. There are **six**
hub-shared networks, split across two Makefiles: the root `create-networks-centralhub` (also run by
`make central-hub`) creates `deploy_central-hub-network` (the hub-internal network) and
`deploy_central-hub-trust-apis-network` (flip-api joins both, FLIP#1344), then `create-networks` forwards to
`trust/Makefile`, which ensures the trust-apis network too (idempotently, for a trust brought up on its own) and
owns the other four — `deploy_fl-net-1/2` (bridge) and `deploy_trust-network-1/2` (overlay, because XNAT
attaches from a swarm stack). Teardown: root `remove-networks` removes the hub-internal one and delegates the
rest.

`fl-net-<N>` is the FL data plane, and carries exactly two services: the hub's `fl-server-net-<N>` and each
trust's `fl-client-net-<N>`. It is the FL twin of `central-hub-trust-apis-network` (flip-api ↔ trust-api) —
one hub service meeting one trust service. Everything else hub-side, `flip-api` and `fl-api-net-<N>`
included, stays on the hub-internal network and reaches the FL server there over its control ports. An
fl-client holds no Central Hub URL and no Central Hub credential by design, so it is given no route to one.

Only **trusts** have more than one deployment shape — Compose on a host (`trust/deploy/`, the host prepared by
`trust/deploy/ansible/`) or Helm on Kubernetes (`trust/deploy/helm/`). Both render one stack, so they live with
it; `providers/` holds only cloud infrastructure (today the `AWS/` Terraform root). The hub has one production
target, AWS ECS Fargate, and needs no provider. See [`providers/README.md`](providers/README.md) for the
shape × infrastructure matrix.

## Supported PostgreSQL Versions

FLIP uses AWS RDS PostgreSQL with the following version support policy:

- **Current Version**: PostgreSQL 17 (EOL: November 2029) ✓
- **Minimum Version**: PostgreSQL 15
- **Deprecated**: PostgreSQL 13 (EOL: November 2025) ❌ EXPIRED

**Version Lifecycle:**

| Version | EOL | Status |
| ------- | --- | ------ |
| PostgreSQL 13 | November 2025 | ❌ EXPIRED — do not use |
| PostgreSQL 14 | 12 November 2026 | ⚠️ Below the minimum, do not use |
| PostgreSQL 15 | October 2027 | ⚠️ Deprecating soon |
| PostgreSQL 16 | October 2028 | ✓ Supported |
| PostgreSQL 17 | November 2029 | ✓ Current (Terraform default) |

**Upgrade Policy**: Plan PostgreSQL major version upgrades with a 6-month lead time before EOL. AWS charges premium rates for EOL versions. To change the version, update the `postgres_version` variable in `deploy/providers/AWS/variables.tf`.

## Deployment Architecture

### Prerequisites

The workstation prerequisites for a hub deploy — the GHCR login (a `read:packages` token), AWS CLI SSO, the
`~/.ssh/host-aws` key pair Terraform uploads as the EC2 key pair (`TF_VAR_flip_keypair` /
`TF_VAR_ec2_public_key_path` customise it), the AWS profile aliases — are in
[Deploy the Central Hub → Prerequisites](https://londonaicentreflip.readthedocs.io/en/latest/deploy-flip/deploy-central-hub.html#prerequisites).

### Final configuration

#### Verify AWS SES email address

Verifying the SES sender identity (and re-verifying an expired one) and checking email end to end:
[Deploy the Central Hub → Email setup](https://londonaicentreflip.readthedocs.io/en/latest/deploy-flip/deploy-central-hub.html#email-setup).

#### Cognito Email Configuration

The Cognito invitation and password-reset emails are Terraform-managed templates; their files, placeholders and
local test utility are in [`providers/AWS/README.md` → Email Templates](providers/AWS/README.md#email-templates).
Cognito sends them itself, not through SES.

#### Cognito MFA Administration

FLIP enforces TOTP (Time-based One-Time Password) MFA for every Cognito user. The enforcement lives in the **application layer**, not in the Cognito pool configuration — the pool's `mfa_configuration` is deliberately set to `OPTIONAL`. This section explains why, how administrators reset MFA for other users, and how an administrator who has lost their own authenticator can recover via the AWS CLI.

##### Why app-layer MFA (not pool `mfa_configuration = "ON"`)

Cognito exposes no admin API to delete a user's verified TOTP secret. With the pool set to `ON`, calling `AdminSetUserMFAPreference` with `Enabled=False` leaves the old secret registered in Cognito — at the next sign-in, Cognito still issues a `SOFTWARE_TOKEN_MFA` challenge and asks the user for a code their (lost) authenticator can no longer generate. The account becomes permanently locked out short of deleting and recreating the user.

With `mfa_configuration = "OPTIONAL"`, disabling the preference actually takes effect: Cognito signs the user in without a challenge. The application layer then catches the user — `flip-api` `verify_token` checks whether `SOFTWARE_TOKEN_MFA` is present in the user's `UserMFASettingList` and returns 403 if not, and the `flip-ui` router guard routes the user to the post-auth enrolment page where they mint a fresh TOTP secret. First-time users follow the same path: they sign in with their temporary password, the app sees `mfaEnabled=false`, and they are walked through enrolment before any protected route is reachable.

SMS MFA is intentionally disabled — it would introduce an SNS dependency and reintroduce SIM-swap risk. The rationale is documented inline at `deploy/providers/AWS/modules/cognito/main.tf` (around the `mfa_configuration` line) and the enforcement point lives in `flip-api/src/flip_api/auth/dependencies.py` (`verify_token`).

##### `ENFORCE_MFA` flag

The MFA gate is controlled by `flip-api`'s `ENFORCE_MFA` setting. The Settings default is `true` — when active, `verify_token` returns 403 for any signed-in user without an active TOTP and the UI's router guard redirects them through enrolment.

The dev override lives in `deploy/compose.development.yml` (`ENFORCE_MFA=false`) so local development doesn't force enrolment on a burner authenticator. **The flag is intentionally not exposed in `.env.development.example` or AWS Secrets Manager** — the Settings default (`true`) is the canonical secure anchor. `deploy/compose.prod.yml` passes `ENFORCE_MFA=${ENFORCE_MFA:-true}` so operators can override it from `.env.stag`/`.env.prod` for testing (e.g. `ENFORCE_MFA=false`), but it falls back to the secure `true` default when unset — do not commit an override into either env file for a real deployment.

Only `false`, `0`, `no` or `off` (trimmed, any case) turn the gate off. `true`, `1`, `yes`, `on`, empty and unset keep it on, and so does any other value — a typo fails closed, with a warning naming the value in the flip-api log.

The flag is mirrored to the UI via `/users/me/mfa/status` (`required: bool`) so the router guard knows when to skip the enrolment redirect.

Development stacks (Keycloak, the only provider there) keep `ENFORCE_MFA=false`: the browser signs in with the OIDC password grant, which cannot enrol or answer TOTP, so `true` there only logs a warning at boot and locks browser users out. TOTP, forgot-password and admin password resets are done in the Keycloak console (`http://localhost:8180/admin`) instead.

##### Resetting MFA for another user

Use *Reset MFA* in the FLIP Admin area: [User management → Reset User MFA](https://londonaicentreflip.readthedocs.io/en/latest/sys-admin/admin-project-and-user-management.html). The UI calls
`POST /users/{user_id}/mfa/reset` on `flip-api`, which runs the same two Cognito operations as the CLI recovery
below, under the FLIP permission model (requires `CAN_MANAGE_USERS`) and with an application-level audit trail.

##### Recovering an administrator account that has lost its authenticator

The AWS CLI runbook for an administrator locked out of their own MFA:
[Deploy the Central Hub → Recovering an administrator's lost MFA](https://londonaicentreflip.readthedocs.io/en/latest/deploy-flip/deploy-central-hub.html#deploy-central-hub-mfa-recovery). It runs
`admin_set_user_mfa_preference` followed by `admin_user_global_sign_out`, exactly what the Cognito identity provider's
`_reset_mfa` (`flip-api/src/flip_api/auth/identity/cognito.py`) does for the UI flow, but outside FLIP and therefore
without FLIP's audit log.

## Deployment Models

### Central Hub

The Central Hub has **one supported production deployment**: ECS Fargate via the Terraform root in
[`deploy/providers/AWS/`](providers/AWS/README.md). The task definitions in `ecs_tasks.tf` (env maps in
`locals.tf`) are the **canonical definition of production container config**. That root deploys onto an
AWS Landing Zone Accelerator estate, where the network and edge are owned by the accelerator — see
[Deploying onto an LZA estate](providers/AWS/README.md#deploying-onto-an-lza-estate)
([FLIP#749](https://github.com/londonaicentre/FLIP/issues/749)). The self-contained single-account mode is no
longer supported and has been removed (FLIP#1479). What the hub costs to run — hub only,
trust hosts excluded because their GPU sizing is a per-trust decision — is on the docs site's
[Deploy the Central Hub → Running costs](https://londonaicentreflip.readthedocs.io/en/latest/deploy-flip/deploy-central-hub.html#running-costs)
section. The ECS FL task definitions serve **both
FL backends** ([FLIP#566](https://github.com/londonaicentre/FLIP/issues/566)): `FL_BACKEND` in the env file
switches the same task families between FLARE and Flower (SuperLink ports/command/creds — Flower
additionally needs `FLOWER_KIT_DATE` and provisioned creds uploaded via
`make -C fl-services/flower provision upload-creds-to-s3`, with `FLOWER_EXTRA_SERVER_SANS` covering the
Cloud Map + public FL hostnames).

> **Deprecated — hub on EC2/compose** ([FLIP#936](https://github.com/londonaicentre/FLIP/issues/936)):
> running the hub via `compose.prod*.yml` on an EC2 or self-managed host is no longer a supported
> deployment target (the former hub EC2 host was long since replaced by a minimal SSM bastion). The
> `compose.prod*.yml` files remain maintained **only** as the local prod-image harness
> (`make up HUB=stag|prod` — the baked images, no dev mounts). When changing production config, change
> Terraform first and update the compose files only as far as the local harness needs.

### Trusts

FLIP supports three trust deployment models:

| Model | Location | Documentation |
| ------- | ---------- | --------------- |
| **Cloud (EC2)** | AWS EC2 (same account as Central Hub) | [`providers/AWS/README.md`](providers/AWS/README.md) |
| **Hybrid / On-Premises** | Any Ubuntu host (home lab, hospital server) | [Deploy a trust on-prem](https://londonaicentreflip.readthedocs.io/en/latest/deploy-flip/deploy-flip-node-on-prem.html) ([`trust/deploy/ansible/README.md`](../trust/deploy/ansible/README.md)) |
| **Kubernetes** | Any K8s cluster 1.28+ (EKS, AKS, on-prem) | [Deploy a trust on Kubernetes](https://londonaicentreflip.readthedocs.io/en/latest/deploy-flip/deploy-flip-node-kubernetes.html) ([`trust/deploy/helm/README.md`](../trust/deploy/helm/README.md)) |

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
| xnat-dcm2niix | `1001:1001` (numeric; no named user in the image) | `USER 1001:1001` in the dcm2niix Dockerfile — xnat-web's uid, so the NIfTIs it writes onto the Container Service's build mount carry the one owner everything else under `xnat-data` has (XNAT reads them back off that mount), and a service the Container Service can hand arbitrary host mounts does not run as root. Not a compose service: a one-shot container the Container Service launches per scan and then reaps, so it sits outside the `cap_drop` regime below, which the compose files impose. |
| flip-db / omop-db | `postgres` | Pre-existing in the base image (`postgres`) |

**Bind-mount ownership.** Because XNAT (`xnat`, UID 1001) and Orthanc (`orthanc`, UID 999) no
longer run as root, the host-side bind-mount source directories must be owned by the matching
UID. For XNAT, the shared Ansible roles (`flip_trust_dirs` in `trust/deploy/ansible/roles/`, composed by
both `deploy/providers/AWS/site.yml` and `trust/deploy/ansible/onprem.yml`) provision
`/opt/flip/xnat/**` as UID 1001. `make -C trust/xnat xnat-reset` is the third path onto the same
invariant and provisions the XNAT directories as UID 1001 too, verifying it afterwards and failing with
the exact `chown` to run rather than leaving a host half-provisioned (FLIP#1095 — it previously used a
hardcoded 1000 on its remote branch and the invoking user's UID on the others). What the three paths
share is the **UID, not the directory**: the playbooks provision `/opt/flip/xnat/**`, while `xnat-reset`
and the compose stacks both read `XNAT_DATA_DIR`, which in stag/prod defaults to the per-slot
`/opt/flip/xnat-trust<N>` so two trusts on one host stay isolated. `xnat-reset` is therefore the path
that provisions the tree XNAT actually writes to, and an ownership change made against the playbooks'
`/opt/flip/xnat` would not reach it. Orthanc's storage directory is not a role's job: on EC2,
`site.yml`'s seed play creates `/opt/flip/orthanc/orthanc-storage` as UID 999 before Orthanc first
starts, and on every other host `make -C trust up-trust` (which the on-prem `up-onprem-trust` calls)
runs `trust/Makefile`'s `ensure_data_dirs`, which chowns it to UID 999 first. If you provision a trust
host outside these paths, you must replicate this ownership or first-boot writes
(archive ingest, SQLite index, log rotation) will fail with EACCES. That
failure is worth recognising because it does not look like a permissions problem: XNAT accepts the
inbound DICOM association, fails the write and aborts it, so the PACS reports a *network* fault
(`Peer aborted Association`), while the same EACCES stops XNAT writing the application logs that
would name the cause. `ensure_data_dirs` chowns through a throwaway alpine container when the
caller is not root, so a developer needs no `sudo` to seed the mock storage directory in dev.

XNAT's dev tree deliberately does **not** follow that convention. `xnat-reset` creates
`trust/xnat/xnat-data-trust<N>/` owned by UID 1001 — unprivileged when the invoking developer is
themselves UID 1001 with its GID (the GitHub runner is too) and already owns every directory in the
tree, under `sudo` otherwise — so on a host whose developer is not UID 1001 that tree is readable
but not writable: deleting it, or running `git clean -fdx` over the checkout, needs `sudo`.
Ownership is the whole of the fix here — it is
what makes XNAT able to ingest at all — and the modes are left at their defaults rather than
widened to buy back the convenience.

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
| fl-client-net-* (both backends, production and development) | *(none)* | Runs non-root (GHSA-8465), and Docker grants effective capabilities only to root — a `cap_add` here would land in the bounding set with `CapEff` still `0`, so it would buy nothing. Flower's `flower-supernode` entrypoint does no chmod/chown at all and its dev mounts are `:ro`. FLARE's dev kit (`provision/workspace-dev/`) is written by `make provision` as the host user. Since FLIP#1171 the image identity is fixed at `flip`/1000/1000 rather than baked from whoever built the image, and the dev composes pass `user: "${UID:-1000}:1000"` — so the container runs as the host user that already owns the kit, and no `chown` (and no capability) is needed. In production the Ansible-provisioned `FL_KIT_DIR` is pre-chowned to the container's UID by `site.yml` / `onprem.yml`; the `DAC_OVERRIDE`/`FOWNER` grant kept for pre-GHSA-8465 root images is gone (FLIP#973). |
| fl-api (FLARE) | `CHOWN` | The `flare-fl-api` image runs as user `flip` (UID 1000, non-root — fixed by FLIP#1171; it previously varied with whoever built the image), so only the `CHOWN` baseline is needed; `DAC_OVERRIDE` and `FOWNER` are inert for non-root processes. |
| fl-server (FLARE) | *(none)* | Runs non-root, so capabilities are inert here as they are for fl-client above — `CapEff` stays `0` and a `cap_add` sits unused in the bounding set. The grants that used to be here (`CHOWN`, `DAC_OVERRIDE`, `FOWNER`) were removed by FLIP#1171 once measurement confirmed they had never taken effect; the comment claiming they were what made the host-owned mounts readable was wrong. What actually reads the provisioned kit's 0600 keys and the operator's 0600 AWS SSO token cache is the matching **uid**: the dev compose runs the container as the host user via `user: "${UID:-1000}:1000"`. |
| fl-server (Flower, development only) | `CHOWN`, `DAC_OVERRIDE` | The dev compose runs the SuperLink as root (see the `user: "0:0"` comment in `compose.development.flower.yml`) to read the host-provisioned 0640 TLS keys and the operator's 0600 SSO token cache; `cap_drop: ALL` strips root's implicit DAC bypass, so `DAC_OVERRIDE` is granted back. Production runs the image's non-root user with instance-role AWS credentials and keeps the `CHOWN` baseline. |
| flip-db, omop-db, xnat-db | `CHOWN`, `DAC_OVERRIDE`, `FOWNER`, `SETUID`, `SETGID` | The official postgres entrypoint runs as root and `gosu`-drops to the `postgres` user (`SETUID`/`SETGID`). On every start it re-runs `chmod 00700 $PGDATA` and a `chown` sweep over the persisted data dir, which on subsequent boots is already owned by `postgres` with mode `0700` — `chmod` on a dir owned by another uid needs `FOWNER` and traversing it needs `DAC_OVERRIDE`. Without them the container exits 1 on a persisted volume (see commit history, FLIP#485). |
| orthanc | `CHOWN` | Runs non-root (UID 999) from PID 1 (the root-image `DAC_OVERRIDE`/`FOWNER` grant is gone, FLIP#863). The storage bind mount is made 999-writable at the provisioning layer rather than fixed up with in-container caps; `CHOWN` is kept as the shared baseline. |
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

XNAT's Container Service plugin launches processing containers — currently
`ghcr.io/londonaicentre/xnat-dcm2niix` (a version-pinned build of dcm2niix; see
`trust/xnat/dcm2niix/`), which every project created with DICOM→NIfTI conversion enabled
triggers automatically on scan archive. That GHCR package must be **public** — the Container
Service pulls it with no credentials, and org packages default to private, so a first publish (or
any package recreate) needs a one-off operator visibility flip; see
[`trust/xnat/README.md`](../trust/xnat/README.md#operator-action-the-ghcr-package-must-be-public).
It used to do this through `/var/run/docker.sock` mounted straight into `xnat-web`,
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
registered-but-unlaunchable dcm2niix command behind. The same script registers `ghcr.io` as a
credential-less Container Service image host: swarm-mode launches resolve registry auth from the
image-host list, and with no entry matching the image's registry hostname they NPE before any
service is created — even though the public image needs no credentials (see
`trust/xnat/README.md`).

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
`trust/Makefile` `-include`s so every trust-internal container inherits it). See the
[public security model](../docs/source/security.rst#trust-internal-service-authentication) for the full threat model.

FL clients (trust side) **do not** have Central Hub API credentials. Only the fl-server communicates with flip-api.
FL clients relay metrics and exceptions to the fl-server, which forwards them to the Central Hub.

### Payload Encryption (flip-api ↔ trust-api, and the project id the FL client carries)

On top of TLS, three things are encrypted under the platform-wide `AES_KEY_BASE64`: every hub → trust task
payload, the encrypted project id the hub hands to FL clients (which forward it to imaging-api and
data-access-api on every image download and cohort call), and the XNAT set-password link imaging-api returns
in a task result (FLIP-PT-079 — never a password). Since FLIP#1179 the cipher is **AES-256-GCM** in a
versioned envelope — base64 of `{"v": 1, "kid": "shared", "iv": <b64 12-byte nonce>, "ct": <b64 ciphertext||tag>}`
— with the version, the key id and a **context** bound into the authentication tag, so a payload altered anywhere
between the sending and the receiving service fails decryption outright instead of decrypting to altered content.
The context is not carried on the wire; both sides derive it from what they already know (`task:<task_type>` for a
task payload, `project_id` for the project id, `xnat_setup_path` for the set-password link), so a task payload
cannot be re-targeted at a different handler by rewriting the unauthenticated `task_type` beside it. What is *not*
covered: the trust's own responses (cohort results, task outcomes) travel back to the hub as plain JSON under TLS
only.

Two operational consequences, both with the operator procedure in the manual's
[Upgrading across the payload-encryption change](https://londonaicentreflip.readthedocs.io/en/latest/deploy-flip/deploy-central-hub.html#upgrading-across-the-payload-encryption-change):

- **The key must be byte-identical on the hub and on every trust container that decrypts** (trust-api,
  imaging-api, data-access-api), and decode to exactly 32 bytes: every service refuses a shorter key at load rather
  than silently running AES-128/192 under the same envelope. A mismatch fails closed — `Invalid payload: failed
  authentication` at trust-api, a **400** from imaging-api / data-access-api (a payload that is not an envelope at
  all is a 400 `... is not a valid envelope`); a 500 is a key-load or cipher fault, not a mismatch. On stag/prod the
  hub's copy is what the CI apply wrote into Secrets Manager, recoverable with
  `deploy/providers/AWS/scripts/reconcile_ci_env.py`.
- **There is no compatibility with the pre-FLIP#1179 AES-CBC format**, so a hub and its trusts cross that boundary
  together, as a flag day under Deployment Mode.

Per-trust keys — a distinct key per trust, so a compromised trust can neither read nor forge another's traffic —
are the follow-up in FLIP#845; they add keyring entries without changing the wire format.

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
