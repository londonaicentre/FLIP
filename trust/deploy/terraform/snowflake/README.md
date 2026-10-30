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

# Snowflake trust infrastructure, workload tier (FLIP#1581)

The OpenTofu root a FLIP trust node is deployed with: the trust database and its schemas, the
compute pools, the image repository, the encrypted stages, the nightly pool-suspend task, and the
grants on those objects to the service, OMOP and observer roles. The warehouse is not here: the
platform tier owns it, with the resource monitor attached (see "Cost guards").

It is the **workload tier** of a two-tier split (epic
[#1589](https://github.com/londonaicentre/FLIP/issues/1589), decided 2026-10-20). It is applied
**routinely**, as `FLIP_<CODE>_DEPLOY_USER` (`TYPE = SERVICE`, key-pair auth), whose role holds
four account privileges and nothing more:

```
CREATE DATABASE, CREATE COMPUTE POOL, BIND SERVICE ENDPOINT, EXECUTE TASK
```

Everything else about the account, the roles themselves, the deploy user, that account grant, the
egress integration, the account network/password/session policies, the event table and the
`EVENT_TABLE` parameter, and the resource monitor, lives in the break-glass root next door,
[`../snowflake-platform`](../snowflake-platform/README.md), applied rarely by a **person** user
with **MFA**. The two roots keep **separate state**, so a routine apply cannot reach an account
security object even by mistake, and the pytest guards in `tests/` fail this root if a role, a
user, an integration, an account policy, an account parameter or an `on_account` grant ever
appears in it.

There is **no remote-state coupling** between the roots: both derive every name from `trust_code`
by the contract in #1589.

It creates **infrastructure only**. The SPCS service specs and the node lifecycle targets
(`mirror-images`, `sync-kit`, `up`, `down`, `suspend`, `status`) land in `trust/deploy/snowflake/`
under [#1582](https://github.com/londonaicentre/FLIP/issues/1582), which consumes this root's
outputs. Per the layout decided in [#1438](https://github.com/londonaicentre/FLIP/issues/1438) this
root has its own state and **reads nothing from a hub**: no hub state, no hub credentials, no hub
env file. The kit file is the trust's only coupling to a hub.

Not production yet. The target is a test account proving the implementation, built to production
security standards so an NHS trust can use the same code.

## Object names

Every name comes from `locals.tf` and from the shared contract in #1589, so this root, the
platform root and #1582 derive the same names from the same `trust_code`. With
`trust_code = GSTT`:

| Object | Name | Created by |
| ------ | ---- | ---------- |
| Roles | `FLIP_GSTT_DEPLOY`, `FLIP_GSTT_SVC`, `FLIP_GSTT_OMOP_READER`, `FLIP_GSTT_OMOP_LOADER`, `FLIP_GSTT_OBSERVER`, `FLIP_GSTT_CONVERTER` | platform root |
| Deploy user | `FLIP_GSTT_DEPLOY_USER` (TYPE = SERVICE, key pair) | platform root |
| Egress integration | `FLIP_GSTT_HUB_EAI` (optional) | platform root |
| Resource monitor | `FLIP_GSTT_RM` | platform root (attached to the warehouse there) |
| Event table | `FLIP_PLATFORM.OBS.FLIP_EVENTS` | platform root |
| Database | `FLIP_TRUST_GSTT`, schemas `APP`, `OMOP`, `STAGES`, `OBS` | here |
| Warehouse | `FLIP_GSTT_WH` (XSMALL) | platform root |
| Compute pools | `FLIP_GSTT_POOL`, `FLIP_GSTT_JOB_POOL`, `FLIP_GSTT_GPU_POOL` (optional) | here |
| Stages (`STAGES`, all SSE) | `FL_KIT`, `SEED`, `XNAT_ARCHIVE`, `IMAGES`, `CONVERTER_IO`, `GOVERNANCE`, `COHORT_SNAPSHOTS` | here |
| Image repository | `FLIP_TRUST_GSTT.APP.IMAGES` | here |
| Converter procedures | `FLIP_TRUST_GSTT.APP.CONVERT_SCAN`, `CONVERT_STATUS` (owned by `CONVERTER`) | here |

`FLIP_<CODE>_ADMIN` is **retired**. An account bootstrapped with it needs the migration in the
[platform README](../snowflake-platform/README.md#migrating-an-account-bootstrapped-with-flip_code_admin).

## Security posture

- **Least privilege by construction.** This root's role can create a database and a compute pool,
  bind a service endpoint, and run the tasks it owns (the nightly pool suspend). It uses the trust
  warehouse (USAGE, OPERATE, MONITOR) but cannot create, modify or own one. It cannot create a role, a user, an integration or a
  network policy, and it has no `MANAGE GRANTS`.
- **Grants work through ownership.** The deploy role owns every object here, and an owner may
  grant privileges on its own objects without `MANAGE GRANTS`. The platform tier granted the four
  consumer roles to the deploy role, so this root may name them. The deploy role grants itself
  nothing: it owns what it creates.
- **No secret value passes through Terraform.** The root creates no `SECRET` object and holds no
  password field. `make sync-kit` (#1582) writes the kit's values into the `APP` schema directly.
- **Grants on secrets are declared, not the secrets themselves.** `grants.tf` carries a FUTURE
  grant (`USAGE` on future `SECRETS` in `APP`) for the service role, so every secret `sync-kit`
  later creates is covered without this root naming or reading one. Secrets that already exist when
  the root is first applied are not covered by a future grant: `sync-kit` issues `GRANT USAGE` for
  those itself. In practice the order is apply first, `sync-kit` second, so the future grant does
  the work. A future grant inside a schema is an owner privilege, so it needs no `MANAGE GRANTS`.
- **Nothing is granted to `PUBLIC`,** and the database's `PUBLIC` schema is dropped at creation.
- **No `ACCOUNTADMIN` anywhere.** `terraform_role` is validated against `FLIP_<CODE>_DEPLOY`, so a
  privileged role cannot be passed to this root by mistake.
- **Egress.** This root creates no network rule and no integration. `enable_hub_egress` only tells
  it whether the platform root created `FLIP_<CODE>_HUB_EAI`, so the name can be reported to
  #1582's service specs. `USAGE` on the integration is granted by the platform tier.
- **No public endpoints.** Service endpoints are `public: false` in #1582's specs; nothing here
  creates one.

## Cost guards

A trust node on SPCS bills per compute-pool node while the pool runs, and **resource monitors do
not cover compute pools**. Three independent guards, all on by default:

1. `pool_auto_suspend_secs` (default 600) on every pool, and pools are created suspended. The
   variable refuses 0.
2. A nightly task, `FLIP_<CODE>_SUSPEND_POOLS`, suspends every pool at `suspend_pools_cron`
   (default 22:00 Europe/London), so a forgotten `make up` costs one evening.
3. `FLIP_<CODE>_RM`, the platform tier's monthly resource monitor, attached to the trust warehouse:
   notify at 50/75/90 per cent, suspend at 95, suspend immediately at 100. Assigning a monitor to
   a warehouse needs `ACCOUNTADMIN`, so the platform tier owns the warehouse and attaches it; the
   deploy role gets USAGE, OPERATE and MONITOR only, so the deploy key can neither detach the
   monitor nor recreate the warehouse without it. Apply the platform root first.

## Prerequisites

- OpenTofu (`tofu`) 1.9 or later. `terraform` works as a fallback, but local state encryption is an
  OpenTofu feature, so the test-account path assumes `tofu`.
- A Snowflake account (the test account is AWS eu-west-2, Enterprise edition).
- **The platform root applied first.** It creates the roles and the deploy user this root signs in
  as.
- Credentials in the environment, never in a variable:

  ```bash
  export SNOWFLAKE_USER=FLIP_GSTT_DEPLOY_USER
  export SNOWFLAKE_AUTHENTICATOR=SNOWFLAKE_JWT
  export SNOWFLAKE_PRIVATE_KEY="$(cat ~/.snowflake/flip_gstt_deploy.p8)"   # the encrypted PEM itself; the provider has no *_PATH variable
  ```

  `~/.snowflake/connections.toml` works too. The provider block in `providers.tf` sets only the
  organization, account, role and warehouse, all from variables.

## Bootstrap

There is no hand-written bootstrap any more: it **is** the platform root. Apply that once, with a
person user holding `ACCOUNTADMIN` and MFA, then come back here. See
[`../snowflake-platform/README.md`](../snowflake-platform/README.md), and keep a second emergency
`ACCOUNTADMIN` person user with separate MFA and offline credentials as it describes.

## State

The backend is a **partial configuration**: `backend.tf` fixes the type, every setting arrives
through `-backend-config`, which the Makefile passes as `BACKEND_CONFIG`. The platform root uses a
different state file name (`terraform-platform.<env>.tfstate`), so the two tiers can never share
one.

**Test account: local state plus OpenTofu state encryption.**

```bash
cp encryption.tf.example encryption.tf          # gitignored
read -rsp "State passphrase: " TF_VAR_state_passphrase; echo; export TF_VAR_state_passphrase
make init
```

The whole encryption block can be supplied through the `TF_ENCRYPTION` environment variable
instead, keeping the passphrase out of the working tree entirely. Both state and plan files are
encrypted, and `enforced = true` means a plaintext state file is refused rather than silently read.

**Production: a site-chosen remote backend.** Drop a gitignored `backend_override.tf` beside
`backend.tf` declaring, for example, `terraform { backend "s3" {} }`, then re-run
`make init BACKEND_CONFIG=...`. Nothing else in the root changes.

## Usage

```bash
cd trust/deploy/terraform/snowflake
cp terraform.tfvars.example terraform.development.tfvars   # gitignored; fill it in
make plan                 # writes snowflake.tfplan
make apply                # shows the plan, asks, applies it
make output               # the names #1582's Makefile consumes, as JSON
make destroy
make lint                 # fmt, validate, tflint, checkov
make test                 # the credential-free pytest guards
```

`PROD` selects the tfvars and state file names through `deploy/env_mode.mk`
(`terraform.$(ENV).tfvars`, `terraform.$(ENV).tfstate`); there is no other per-environment
behaviour yet. No target skips the confirmation prompt.

## Outputs

`make output` prints what #1582 reads: `database_name`, `schema_names`, `image_repository_name`,
`image_repository_url`, `compute_pool_names`, `stage_names`, `warehouse_name`, `role_names`,
`deploy_user_name`, `external_access_integration_name` and `fl_net_image_prefixes`. Names only: no
credential, no account identifier. The role, user and integration names are derived from
`trust_code` by convention, not read from the platform root's state.

## CI

Static and credential-free, in `validate_terraform.yml`: `tofu fmt`, `tofu validate`, tflint
(bundled terraform ruleset, there is no Snowflake plugin), checkov, and the pytest guards in
`tests/`, for **both** roots. No Snowflake credential exists in CI, so nothing plans or applies
there. The workflow's path filter covers `trust/deploy/terraform/**` only, so a change here never
triggers the hub's plan or apply workflows.

## Open points

- **No budget resource.** Snowflake budgets are a native application, with no provider resource, so
  the credit cap is the platform tier's resource monitor plus the pool guards here. A budget is
  still worth setting by hand on an On-Demand account (#1586).
- **Preview resources.** Stages, compute pools and image repositories are still preview features in
  the 2.x provider and are enabled explicitly in `providers.tf`. A provider upgrade that promotes
  or renames one of them fails there rather than drifting silently.
- **Services are not here yet.** `manage_services` is a placeholder variable for #1582 and changes
  nothing.
- **Trial accounts have no external network access,** so the platform tier's `enable_hub_egress`
  stays false until the account is converted to On-Demand (#1586). Flower's run-time `uv sync`
  needs PyPI egress; either pre-bake the dependencies or add those endpoints to the platform root's
  `fl_server_endpoints` when that happens.

## The NIfTI converter (FLIP#1578)

dcm2niix runs as an SPCS job service, one per scan, launched only by `CALL APP.CONVERT_SCAN(project,
session, scan)` (`converter.tf`). The reason is token scope: a service's OAuth token carries the role
that owns it, and SPCS refuses `GRANT OWNERSHIP ON SERVICE`, so whoever runs `EXECUTE JOB SERVICE`
decides the container's token. The procedures run with the rights of `FLIP_<CODE>_CONVERTER`, the
only role holding `CREATE SERVICE` at rest, with `READ` on the archive and `READ`/`WRITE` on
`CONVERTER_IO`. The services role gets `USAGE` on the two procedures and nothing else of the
converter's, so imaging-api can start a conversion but no service can start an arbitrary container.

The procedure takes three identifiers, refuses any that is not `[A-Za-z0-9_-]` before building SQL,
and launches a spec written here: image (the mirrored `xnat-dcm2niix` at the pinned tag), job pool,
command and mounts. Stage volumes cannot be mounted read-only, so the job mounts one session's
archive subpath and writes under `CONVERTER_IO/<project>/<session>/<scan>`; imaging-api registers
the output in XNAT over REST. `CALL APP.CONVERT_STATUS(...)` returns the job's SPCS status JSON
(`[]` before it exists).

