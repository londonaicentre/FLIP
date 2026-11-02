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

# Snowflake bootstrap tier, break glass (epic #1589)

The only root applied with `ACCOUNTADMIN`. Once per account, then rarely, by the just-in-time
operator (`make jit-grant` / `make jit-revoke`, passkey MFA in Snowsight, never from CI). It holds
what nothing else may:

- `FLIP_PLATFORM_CI` and `FLIP_PLATFORM_CI_READ`, and the two CI service users that hold them
  (`FLIP_CI_APPLY_USER`, `FLIP_CI_PLAN_USER`), which log in only with a GitHub OIDC token from the
  account's `sf-stag` / `sf-prod` environment: no password, no key;
- the resource monitor and the trust warehouse it caps;
- the `EVENT_TABLE` account parameter;
- the account password, session and network policies, in their own `FLIP_ADMIN` database;
- the hub egress rule and external access integration (off by default).

The platform root ([`../snowflake-platform`](../snowflake-platform/README.md)) is then applied by CI as
`FLIP_PLATFORM_CI`. What that role may do is defined in `locals.tf` and `ci.tf` here:

| Holds | Why |
| --- | --- |
| `CREATE ROLE`, `CREATE USER`, `CREATE DATABASE`, `EXECUTE TASK` | the trust roles, the deploy user, `FLIP_PLATFORM`, the prune task |
| `CREATE DATABASE`, `CREATE COMPUTE POOL`, `BIND SERVICE ENDPOINT`, `EXECUTE TASK` **with grant option** | to pass exactly these to `FLIP_<CODE>_DEPLOY` |
| `USAGE`, `OPERATE`, `MONITOR` on the warehouse **with grant option** | to give the trust roles its use |
| `IMPORTED PRIVILEGES` on `SNOWFLAKE` | the cost views read `ACCOUNT_USAGE` |

Never: `MANAGE GRANTS`, `CREATE INTEGRATION`, any `APPLY <policy>`, a system role, or ownership of
anything it did not create. `FLIP_PLATFORM_CI` sits under `SYSADMIN`, so the break-glass operator can
see and repair what CI owns. The remaining risk, plainly: CI can create a user holding any FLIP
role. It cannot reach a system role, an account policy or another team's objects.
`tests/test_bootstrap_guards.py` pins all of this, and also checks that the platform root declares
only resource types this role can manage.

## A new account

```
make jit-grant                                  # run the printed SQL in Snowsight
make init plan apply   (set_event_table = false) # CI role, users, warehouse, monitor, policies
# first platform apply (CI, or the operator with FLIP_PLATFORM_CI): creates the event table
make plan apply        (set_event_table = true)
make jit-revoke                                 # run it, then verify from the operator session
```

## An account applied before the split (SFTEST)

1. `make init plan apply` here with `set_event_table = false`. That creates the CI role and users,
   `FLIP_ADMIN` and new policies. It does not yet own the warehouse or monitor, so the plan must
   not show them as new: run step 2 first if it does.
2. In `../snowflake-platform`: `make split-state` (forgets, destroys nothing). Then here:
   `make adopt-state TRUST_CODE=<CODE>`.
3. `make adopt-sql TRUST_CODE=<CODE>` prints the Snowsight SQL that detaches and drops the old
   policies and hands the platform objects to `FLIP_PLATFORM_CI` (`COPY CURRENT GRANTS`).
4. `make plan apply` here with `set_event_table = true`, then a platform plan as `FLIP_PLATFORM_CI`:
   it must show no changes.

State: `terraform-bootstrap.<env>.tfstate`, OpenTofu-encrypted (`encryption.tf.example`).
