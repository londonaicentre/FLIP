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

# Snowflake platform tier, break glass (FLIP#1581, epic #1589)

**Read this before running anything here.** This is the break-glass root. It creates the Snowflake
account's security objects and nothing else:

- the five `FLIP_<CODE>_*` roles and the role hierarchy (`DEPLOY`, `SVC`, `OMOP_READER`,
  `OMOP_LOADER`, `OBSERVER`), nothing granted to `PUBLIC`;
- the deploy `SERVICE` user, public key only;
- the **only** account-level grant in the whole design: four privileges to `FLIP_<CODE>_DEPLOY`;
- the egress network rule and the external access integration (off by default);
- the account network, password and session policies;
- the event table and the `EVENT_TABLE` account parameter, in a small platform-owned database;
- the resource monitor.

It is applied **rarely**, by a **person** user with **MFA** holding `ACCOUNTADMIN` (or
`SECURITYADMIN` where that is enough), and **never from CI**. Routine work belongs in
[`../snowflake`](../snowflake/README.md), the workload root, applied as `FLIP_<CODE>_DEPLOY_USER`
with key-pair auth. The two roots keep **separate state**: that is the point of the split. A
routine apply cannot touch a role, a user, an account grant or an account policy, because none of
those objects is in its state file.

## The two tiers

| | Platform (here) | Workload (`../snowflake`) |
| --- | --- | --- |
| Identity | person user, MFA | `FLIP_<CODE>_DEPLOY_USER` (`TYPE = SERVICE`, key pair) |
| Role | `ACCOUNTADMIN` / `SECURITYADMIN` | `FLIP_<CODE>_DEPLOY` |
| Frequency | rarely, by hand | every deploy |
| Creates | roles, deploy user, account grants, EAI, account policies, event table, resource monitor | database, schemas, warehouse, compute pools, image repository, stages, nightly task, grants on those objects |
| State | `terraform-platform.<env>.tfstate` | `terraform.<env>.tfstate` |
| In CI | static checks only | static checks only |

The **account privileges** `FLIP_<CODE>_DEPLOY` holds are exactly:

```
CREATE DATABASE, CREATE WAREHOUSE, CREATE COMPUTE POOL, BIND SERVICE ENDPOINT
```

Deliberately absent: `CREATE ROLE`, `CREATE USER`, `CREATE INTEGRATION`, `CREATE NETWORK POLICY`,
`MANAGE GRANTS`, any `APPLY <policy>`, `MONITOR USAGE`. A compromised deploy key therefore cannot
mint an identity, open an egress path, detach an account policy or re-grant anything it does not
own. Both roots' pytest guards fail if that list ever grows.

### How the tiers hand over

The workload role **owns** every object it creates, and an owner may grant privileges on its own
objects **without** `MANAGE GRANTS`. What an owner cannot do is name a role it does not hold. So
this root grants `SVC`, `OMOP_READER`, `OMOP_LOADER` and `OBSERVER` **to** `FLIP_<CODE>_DEPLOY`
once (`roles.tf`), and from then on a routine apply can say "grant `SELECT` on this table to
`FLIP_<CODE>_OMOP_READER`" entirely within its own privileges. Ownership stays with the deploy
role throughout.

The resource monitor is the one object the workload tier has to touch without owning: attaching a
monitor to a warehouse needs `MODIFY` on the monitor, which this root grants to the deploy role.

There is **no remote-state data source** between the roots. Both derive every name from
`trust_code` by the contract in [#1589](https://github.com/londonaicentre/FLIP/issues/1589), so
neither can read, lock or corrupt the other's state.

## Object names

With `trust_code = GSTT`:

| Object | Name |
| ------ | ---- |
| Roles | `FLIP_GSTT_DEPLOY`, `FLIP_GSTT_SVC`, `FLIP_GSTT_OMOP_READER`, `FLIP_GSTT_OMOP_LOADER`, `FLIP_GSTT_OBSERVER` |
| Deploy user | `FLIP_GSTT_DEPLOY_USER` (`TYPE = SERVICE`, key pair) |
| Egress integration | `FLIP_GSTT_HUB_EAI` (optional, off by default) |
| Account policies | `FLIP_GSTT_ADMIN_NP`, `FLIP_GSTT_PASSWORD_POLICY`, `FLIP_GSTT_SESSION_POLICY` |
| Resource monitor | `FLIP_GSTT_RM` |
| Platform database | `FLIP_PLATFORM`, schemas `OBS` (the event table) and `POLICIES` |
| Event table | `FLIP_PLATFORM.OBS.FLIP_EVENTS` |

`FLIP_<CODE>_ADMIN` is **retired** (two-tier decision, 2026-10-20, epic #1589). See the migration
note below.

The event table lives in `FLIP_PLATFORM`, not in `FLIP_TRUST_<CODE>`, because the `EVENT_TABLE`
account parameter must point somewhere that outlives any one trust: in the trust database a
workload `make destroy` would silently turn the account's logging off. `FLIP_<CODE>_OBSERVER` is
granted `USAGE` on the database and schema and `SELECT` on the event table.

## Credentials

Every credential comes from the environment. Nothing here accepts one as a Terraform variable: a
password or a passcode in a variable would land in state and in every plan file.

```bash
export SNOWFLAKE_USER=jane.doe            # a PERSON user with MFA
export SNOWFLAKE_PASSWORD=...             # read from your password manager, never typed into a file
export SNOWFLAKE_PASSCODE=...             # the current TOTP code
```

`authenticator = "USERNAMEPASSWORDMFA"` (the default) pairs the password with that passcode.
`authenticator = "EXTERNALBROWSER"` delegates to the account's IdP and needs no password in the
environment at all; use it wherever the account is federated.

`platform_role` has **no default**: the operator names it in their gitignored tfvars file, so no
privileged role name is committed.

### Keep a second emergency account admin

Create a **second** `ACCOUNTADMIN` person user with its **own** MFA enrolment and credentials held
offline (a sealed envelope in the usual place for break-glass credentials). Snowflake will not let
you back in if the only admin's authenticator is lost, and this root cannot help you: it is applied
*through* that identity. Review the pair whenever someone leaves.

## Usage

```bash
cd trust/deploy/terraform/snowflake-platform
cp terraform.tfvars.example terraform.development.tfvars   # gitignored; fill it in
make plan
make apply        # prints the BREAK GLASS banner, asks twice, then applies
make output
make lint         # fmt, validate, tflint, checkov
make test         # the credential-free pytest guards
```

`make apply` and `make destroy` both print a loud banner and require the operator to type
`BREAK GLASS` before anything else happens; `apply` then shows the saved plan and asks a second
time. No target skips a prompt.

State and backend work exactly as in the workload root (partial backend configuration, local state
plus OpenTofu state encryption on the test account, a site-chosen remote backend in production),
but the state **file name differs** so the two roots can never share one.

## Migrating an account bootstrapped with FLIP_<CODE>_ADMIN

Earlier drafts of #1581 bootstrapped a single `FLIP_<CODE>_ADMIN` role holding `CREATE ROLE`,
`CREATE USER`, `CREATE INTEGRATION`, `CREATE NETWORK POLICY` and `BIND SERVICE ENDPOINT WITH GRANT
OPTION`, and ran every apply as it. That role is retired. On an account already bootstrapped that
way, as `ACCOUNTADMIN`:

```sql
USE ROLE ACCOUNTADMIN;

-- 1. See what it holds and owns before changing anything.
SHOW GRANTS TO ROLE FLIP_GSTT_ADMIN;
SHOW GRANTS ON ACCOUNT;

-- 2. Take the account privileges away.
REVOKE CREATE DATABASE, CREATE WAREHOUSE, CREATE COMPUTE POOL, CREATE ROLE, CREATE USER,
       CREATE INTEGRATION, CREATE NETWORK POLICY, EXECUTE TASK, MONITOR USAGE
  ON ACCOUNT FROM ROLE FLIP_GSTT_ADMIN;
REVOKE BIND SERVICE ENDPOINT ON ACCOUNT FROM ROLE FLIP_GSTT_ADMIN;

-- 3. Move everything it owns to the workload role. Repeat per object type that SHOW GRANTS
--    reported; ownership transfer is explicit in Snowflake.
GRANT OWNERSHIP ON DATABASE FLIP_TRUST_GSTT TO ROLE FLIP_GSTT_DEPLOY COPY CURRENT GRANTS;
GRANT OWNERSHIP ON ALL SCHEMAS IN DATABASE FLIP_TRUST_GSTT TO ROLE FLIP_GSTT_DEPLOY COPY CURRENT GRANTS;
GRANT OWNERSHIP ON ALL TABLES IN DATABASE FLIP_TRUST_GSTT TO ROLE FLIP_GSTT_DEPLOY COPY CURRENT GRANTS;
GRANT OWNERSHIP ON ALL STAGES IN DATABASE FLIP_TRUST_GSTT TO ROLE FLIP_GSTT_DEPLOY COPY CURRENT GRANTS;
GRANT OWNERSHIP ON ALL TASKS IN DATABASE FLIP_TRUST_GSTT TO ROLE FLIP_GSTT_DEPLOY COPY CURRENT GRANTS;
GRANT OWNERSHIP ON WAREHOUSE FLIP_GSTT_WH TO ROLE FLIP_GSTT_DEPLOY COPY CURRENT GRANTS;
GRANT OWNERSHIP ON COMPUTE POOL FLIP_GSTT_POOL TO ROLE FLIP_GSTT_DEPLOY COPY CURRENT GRANTS;
GRANT OWNERSHIP ON COMPUTE POOL FLIP_GSTT_JOB_POOL TO ROLE FLIP_GSTT_DEPLOY COPY CURRENT GRANTS;
GRANT OWNERSHIP ON IMAGE REPOSITORY FLIP_TRUST_GSTT.APP.IMAGES TO ROLE FLIP_GSTT_DEPLOY COPY CURRENT GRANTS;

-- 4. Nothing should hold it any more.
REVOKE ROLE FLIP_GSTT_ADMIN FROM USER FLIP_GSTT_DEPLOY_USER;
REVOKE ROLE FLIP_GSTT_ADMIN FROM ROLE SYSADMIN;

-- 5. Drop it.
DROP ROLE FLIP_GSTT_ADMIN;
```

Then apply this root (it adopts or creates the roles, the user and the account grant), and
re-apply the workload root. Expect the workload plan to show grant churn and nothing destructive;
read it before typing `yes`. If the old state file mixed both tiers, split it with
`tofu state rm` for the moved resources rather than letting the workload root destroy them.

## CI

Static and credential-free, in `validate_terraform.yml`: `fmt`, `validate`, tflint (bundled
terraform ruleset, there is no Snowflake plugin), checkov, and the pytest guards in `tests/`. No
Snowflake credential exists in CI, and this tier is never applied there by design.

## Open points

- **No event-table resource.** The 2.x provider has none, so `observability.tf` creates the table,
  and `grants.tf` grants `SELECT` on it, through `snowflake_execute` statement pairs whose reverts
  undo them. Replace with first-class resources when they appear.
- **`EVENT_TABLE` is account-wide,** so an account hosting two trusts points at the last one
  applied. One trust per account is the tested shape; `FLIP_PLATFORM` is shared when it is not.
- **No budget resource.** Snowflake budgets are a native application with no provider resource, so
  the credit cap here is the resource monitor plus the workload tier's pool guards.
- **Preview resources.** The network rule, the external access integration and the
  password/session/network policy attachments are still preview features in the 2.x provider and
  are enabled explicitly in `providers.tf`.
