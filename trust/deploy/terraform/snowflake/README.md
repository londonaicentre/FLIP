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

# Snowflake trust infrastructure (FLIP#1581)

The OpenTofu root that creates the Snowflake objects a FLIP trust node runs on: roles, the deploy
identity, the database and its schemas, the warehouse, the compute pools, the image repository, the
encrypted stages, the egress rules, the account policies, the event table and the cost guards.

It creates **infrastructure only**. The SPCS service specs and the node lifecycle targets
(`mirror-images`, `sync-kit`, `up`, `down`, `suspend`, `status`) land in `trust/deploy/snowflake/`
under [#1582](https://github.com/londonaicentre/FLIP/issues/1582), which consumes this root's
outputs. Per the layout decided in [#1438](https://github.com/londonaicentre/FLIP/issues/1438) this
root has **its own state and reads nothing from a hub**: no hub state, no hub credentials, no hub
env file. The kit file is the trust's only coupling to a hub.

Not production yet. The target is a test account proving the implementation, built to production
security standards so an NHS trust can use the same code.

## Object names

Every name comes from `locals.tf` and from the shared contract in
[#1589](https://github.com/londonaicentre/FLIP/issues/1589), so this root and #1582 derive the same
names from the same `trust_code`. With `trust_code = GSTT`:

| Object | Name |
| ------ | ---- |
| Roles | `FLIP_GSTT_ADMIN`, `FLIP_GSTT_DEPLOY`, `FLIP_GSTT_SVC`, `FLIP_GSTT_OMOP_READER`, `FLIP_GSTT_OMOP_LOADER`, `FLIP_GSTT_OBSERVER` |
| Deploy user | `FLIP_GSTT_DEPLOY_USER` (TYPE = SERVICE, key pair) |
| Warehouse | `FLIP_GSTT_WH` (XSMALL) |
| Compute pools | `FLIP_GSTT_POOL`, `FLIP_GSTT_JOB_POOL`, `FLIP_GSTT_GPU_POOL` (optional) |
| Egress integration | `FLIP_GSTT_HUB_EAI` (optional) |
| Resource monitor | `FLIP_GSTT_RM` |
| Database | `FLIP_TRUST_GSTT`, schemas `APP`, `OMOP`, `STAGES`, `OBS` |
| Stages (`STAGES`, all SSE) | `FL_KIT`, `SEED`, `XNAT_ARCHIVE`, `IMAGES`, `CONVERTER_IO`, `GOVERNANCE`, `COHORT_SNAPSHOTS` |
| Image repository | `FLIP_TRUST_GSTT.APP.IMAGES` |

## Security posture

- **No secret value passes through Terraform.** The root creates no `SECRET` object and holds no
  password field. `make sync-kit` (#1582) writes the kit's values into the `APP` schema directly.
- **Grants on secrets are declared, not the secrets themselves.** `grants.tf` carries a FUTURE
  grant (`USAGE` on future `SECRETS` in `APP`) for the service role, so every secret `sync-kit`
  later creates is covered without this root naming or reading one. Secrets that already exist when
  the root is first applied are not covered by a future grant: `sync-kit` issues `GRANT USAGE` for
  those itself. In practice the order is apply first, `sync-kit` second, so the future grant does
  the work.
- **Nothing is granted to `PUBLIC`,** and the database's `PUBLIC` schema is dropped at creation.
- **No `ACCOUNTADMIN` in automation.** It is used once, by hand, for the bootstrap below.
- **The deploy identity is a `SERVICE` user with key-pair auth.** Only the public half is an input;
  the private key stays on the operator machine or in a CI secret store.
- **Egress is closed by default.** `enable_hub_egress = false` creates no network rule and no
  external access integration. When enabled, the rule is a `HOST_PORT` list holding exactly the hub
  API on 443 plus the FL server endpoints from the kit.
- **No public endpoints.** Service endpoints are `public: false` in #1582's specs; nothing here
  creates one.
- **Account policies.** A password policy and a session policy are attached to the account (the
  session policy to person users: a `SERVICE` user has no interactive session, and the provider
  refuses both attachment flags at once). `admin_allowed_cidrs` attaches an account network policy;
  it is empty by default so a first apply cannot lock the operator out.

## Cost guards

A trust node on SPCS bills per compute-pool node while the pool runs, and **resource monitors do
not cover compute pools**. Three independent guards, all on by default:

1. `pool_auto_suspend_secs` (default 600) on every pool, and pools are created suspended. The
   variable refuses 0.
2. A nightly task, `FLIP_<CODE>_SUSPEND_POOLS`, suspends every pool at `suspend_pools_cron`
   (default 22:00 Europe/London), so a forgotten `make up` costs one evening.
3. `FLIP_<CODE>_RM`, a monthly resource monitor on the warehouse: notify at 50/75/90 per cent,
   suspend at 95, suspend immediately at 100.

## Prerequisites

- OpenTofu (`tofu`) 1.9 or later. `terraform` works as a fallback, but local state encryption is an
  OpenTofu feature, so the test-account path assumes `tofu`.
- A Snowflake account (the test account is AWS eu-west-2, Enterprise edition).
- Credentials in the environment, never in a variable:

  ```bash
  export SNOWFLAKE_USER=FLIP_GSTT_DEPLOY_USER
  export SNOWFLAKE_AUTHENTICATOR=SNOWFLAKE_JWT
  export SNOWFLAKE_PRIVATE_KEY_PATH=~/.snowflake/flip_gstt_deploy.p8
  ```

  `~/.snowflake/connections.toml` works too. The provider block in `providers.tf` sets only the
  organization, account, role and warehouse, all from variables.

## Bootstrap (one off, by hand)

An account admin runs this once, in Snowsight, so that nothing afterwards needs `ACCOUNTADMIN`:

```sql
USE ROLE ACCOUNTADMIN;
CREATE ROLE IF NOT EXISTS FLIP_GSTT_ADMIN;
GRANT ROLE FLIP_GSTT_ADMIN TO ROLE SYSADMIN;
GRANT CREATE DATABASE, CREATE WAREHOUSE, CREATE COMPUTE POOL, CREATE ROLE, CREATE USER,
      CREATE INTEGRATION, CREATE NETWORK POLICY, EXECUTE TASK, MONITOR USAGE
  ON ACCOUNT TO ROLE FLIP_GSTT_ADMIN;
GRANT BIND SERVICE ENDPOINT ON ACCOUNT TO ROLE FLIP_GSTT_ADMIN WITH GRANT OPTION;
CREATE USER IF NOT EXISTS FLIP_GSTT_DEPLOY_USER TYPE = SERVICE
  RSA_PUBLIC_KEY = '<public key body>';
GRANT ROLE FLIP_GSTT_ADMIN TO USER FLIP_GSTT_DEPLOY_USER;
```

The first apply then runs as `FLIP_GSTT_ADMIN` and adopts the user and role it finds
(`terraform import`, or let the apply fail once and import the two objects). Later applies need
nothing above that role.

## State

The backend is a **partial configuration**: `backend.tf` fixes the type, every setting arrives
through `-backend-config`, which the Makefile passes as `BACKEND_CONFIG`.

**Test account: local state plus OpenTofu state encryption.**

```bash
cp encryption.tf.example encryption.tf          # gitignored
export TF_ENCRYPTION_PASSPHRASE=...             # from your password manager
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
`deploy_user_name`, `external_access_integration_name`, `event_table_name` and
`fl_net_image_prefixes`. Names only: no credential, no account identifier.

## CI

Static and credential-free, in `validate_terraform.yml`: `tofu fmt`, `tofu validate`, tflint
(bundled terraform ruleset, there is no Snowflake plugin), checkov, and the pytest guards in
`tests/`. No Snowflake credential exists in CI, so nothing plans or applies there. The workflow's
path filter covers `trust/deploy/terraform/snowflake/**` only, so a change here never triggers the
hub's plan or apply workflows.

## Open points

- **No event-table resource.** The 2.x provider has none, so `observability.tf` creates the event
  table through `snowflake_execute` (a statement pair whose revert drops it) and wires it to the
  account with the `EVENT_TABLE` account parameter. Replace it with a first-class resource when one
  appears. The parameter is account-wide, so an account hosting two trusts points at the last one
  applied; one trust per account is the tested shape.
- **No budget resource.** Snowflake budgets are a native application, with no provider resource, so
  the credit cap here is the resource monitor plus the pool guards. A budget is still worth setting
  by hand on an On-Demand account (#1586).
- **Preview resources.** Stages, compute pools, image repositories, network rules, the external
  access integration and the password/session/network policy attachments are still preview features
  in the 2.x provider and are enabled explicitly in `providers.tf`. A provider upgrade that
  promotes or renames one of them fails there rather than drifting silently.
- **Services are not here yet.** `manage_services` is a placeholder variable for #1582 and changes
  nothing.
- **Trial accounts have no external network access,** so `enable_hub_egress` stays false until the
  account is converted to On-Demand (#1586). Flower's run-time `uv sync` needs PyPI egress; either
  pre-bake the dependencies or add those endpoints to `fl_server_endpoints` when that happens.
