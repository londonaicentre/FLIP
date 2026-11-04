# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Static guards for the Snowflake PLATFORM root (FLIP#1581, epic #1589).

The break-glass tier. These are the invariants no runtime test can see without a Snowflake
account: the workload role receives EXACTLY four account privileges and no more, nothing is
granted to PUBLIC, the retired FLIP_<CODE>_ADMIN role is gone, the deploy identity is a SERVICE
user with a public key only, egress is off by default and closed when on, and no credential of any
kind is a Terraform variable.
"""

from __future__ import annotations

import re
import subprocess

import pytest
from conftest import DASHBOARD_README, DASHBOARD_SQL, ROOT_DIR, TF_FILES, all_terraform, source
from tf_blocks import hcl_block, resource_blocks

ROOT_FILES = (
    "versions.tf",
    "backend.tf",
    "providers.tf",
    "variables.tf",
    "locals.tf",
    "roles.tf",
    "user.tf",
    "platform_database.tf",
    "network.tf",
    "policies.tf",
    "observability.tf",
    "observability_views.tf",
    "cost.tf",
    "warehouse.tf",
    "grants.tf",
    "outputs.tf",
)

# The complete set of account privileges the workload role may hold (epic #1589).
ALLOWED_ACCOUNT_PRIVILEGES = (
    "CREATE DATABASE",
    "CREATE COMPUTE POOL",
    "BIND SERVICE ENDPOINT",
    "EXECUTE TASK",
)

# Account privileges that would undo the split. None may appear anywhere in this root.
FORBIDDEN_ACCOUNT_PRIVILEGES = (
    "CREATE ROLE",
    "CREATE USER",
    "CREATE INTEGRATION",
    "CREATE NETWORK POLICY",
    "MANAGE GRANTS",
    "MONITOR USAGE",
)

ROLES = ("DEPLOY", "SVC", "OMOP_READER", "OMOP_LOADER", "OBSERVER", "CONVERTER")

SECRET_NAMES = (
    "TRUST_API_KEY",
    "TRUST_INTERNAL_SERVICE_KEY",
    "AES_KEY_BASE64",
    "XNAT_ADMIN_PASSWORD",
    "XNAT_SERVICE_PASSWORD",
    "XNAT_DB_PASSWORD",
    "ORTHANC_PASSWORD",
)


@pytest.mark.parametrize("name", ROOT_FILES)
def test_the_root_has_its_files(name):
    assert (ROOT_DIR / name).is_file(), name


@pytest.mark.parametrize("path", TF_FILES, ids=lambda p: p.name)
def test_apache_header(path):
    assert "Licensed under the Apache License, Version 2.0" in path.read_text(), path.name


# --- the two-tier contract ------------------------------------------------------------------------


@pytest.mark.parametrize("role", ROLES)
def test_the_six_roles_exist(role):
    assert f'"${{local.prefix}}_{role}"' in source("locals.tf"), role


def test_the_admin_role_is_retired():
    text = all_terraform()
    assert '_ADMIN"' not in text.replace("_ADMIN_NP", ""), (
        "FLIP_<CODE>_ADMIN is retired: the two-tier split replaced it (epic #1589)"
    )


def test_the_workload_role_gets_exactly_the_allowed_account_privileges():
    block = hcl_block(source("locals.tf"), "locals {")
    listed = re.search(r"deploy_account_privileges = \[(.*?)\]", block, re.S)
    assert listed, "locals.tf must spell out the account privileges the workload role holds"
    privileges = re.findall(r'"([^"]+)"', listed.group(1))
    assert privileges == list(ALLOWED_ACCOUNT_PRIVILEGES), privileges


def test_the_account_grant_uses_that_list_and_nothing_else():
    blocks = [
        block
        for block in resource_blocks(source("grants.tf"), "snowflake_grant_privileges_to_account_role")
        if "on_account        = true" in block or "on_account = true" in block
    ]
    assert len(blocks) == 1, "exactly one account-level grant, to the workload role"
    assert "privileges        = local.deploy_account_privileges" in blocks[0]
    assert "account_role_name = snowflake_account_role.deploy.name" in blocks[0]


@pytest.mark.parametrize("privilege", FORBIDDEN_ACCOUNT_PRIVILEGES)
def test_no_forbidden_account_privilege_is_granted(privilege):
    for path in TF_FILES:
        assert f'"{privilege}"' not in path.read_text(), f"{path.name} grants {privilege}"


def test_the_consumer_roles_are_granted_to_the_deploy_role():
    """So the workload root can grant on its own objects without MANAGE GRANTS."""
    block = hcl_block(source("roles.tf"), 'resource "snowflake_grant_account_role" "to_deploy"')
    assert block, "the platform tier must hand the consumer roles to the deploy role"
    for role in ("svc", "omop_reader", "omop_loader", "observer", "converter"):
        assert re.search(rf"{role}\s+= snowflake_account_role.{role}.name", block), role
    assert "parent_role_name = snowflake_account_role.deploy.name" in block


# --- grants ---------------------------------------------------------------------------------------


def test_nothing_is_granted_to_public():
    text = all_terraform()
    assert not re.search(r'account_role_name\s*=\s*"PUBLIC"', text), "PUBLIC must never receive a grant"
    assert "PUBLIC" not in re.sub(r"#.*", "", text).replace("drop_public_schema_on_creation", ""), (
        "the only mention of PUBLIC may be dropping the database's PUBLIC schema"
    )


def test_no_grant_uses_all_privileges_or_grant_option():
    text = source("grants.tf")
    assert "all_privileges" not in text, "least privilege: list the privileges"
    assert "with_grant_option" not in text, "no role may pass its privileges on"


def test_accountadmin_is_never_a_configured_value():
    """The break-glass role is supplied at apply time, never defaulted in tracked Terraform."""
    for path in TF_FILES:
        assert not re.search(r'=\s*"ACCOUNTADMIN"', path.read_text()), (
            f"{path.name}: the privileged role is a variable with no default, set in the operator's tfvars"
        )
    block = hcl_block(source("variables.tf"), 'variable "platform_role"')
    assert not re.search(r"^\s*default\s*=", block, re.M), "platform_role must be supplied at apply time"


# --- credentials ------------------------------------------------------------------------------------


def test_the_break_glass_operator_uses_mfa():
    block = hcl_block(source("variables.tf"), 'variable "authenticator"')
    assert "USERNAMEPASSWORDMFA" in block
    assert "EXTERNALBROWSER" in block
    assert 'default     = "SNOWFLAKE_JWT"' in block, "the just-in-time key-pair operator is the default"
    provider = hcl_block(source("providers.tf"), 'provider "snowflake"')
    assert "authenticator     = var.authenticator" in provider


@pytest.mark.parametrize("field", ["password", "passcode", "private_key", "token"])
def test_no_credential_is_a_terraform_variable(field):
    for path in TF_FILES:
        assert not re.search(rf'variable "[a-z_]*{field}[a-z_]*"', path.read_text()), (
            f"{path.name}: {field} must come from the environment, never from a variable"
        )


def test_the_deploy_identity_is_a_service_user_with_a_public_key():
    block = hcl_block(source("user.tf"), 'resource "snowflake_service_user" "deploy"')
    assert block, "the deploy identity must be a SERVICE user"
    assert "rsa_public_key = var.deploy_user_public_key" in block
    assert not re.search(r"^\s*password\s*=", block, re.M)


def test_no_person_user_is_managed_here():
    assert not resource_blocks(all_terraform(), "snowflake_user"), (
        "person users, including the break-glass operator, are created by hand and never by Terraform"
    )


@pytest.mark.parametrize("secret", SECRET_NAMES)
def test_no_kit_secret_is_named_by_terraform(secret):
    for path in TF_FILES:
        assert secret not in path.read_text(), f"{path.name} names {secret}: secrets are written by make sync-kit"


def test_no_secret_resource_anywhere():
    text = all_terraform()
    assert "snowflake_secret_with" not in text, "Terraform must create no SECRET: it would hold the value in state"
    assert not re.search(r"^\s*password\s*=", text, re.M), "no resource in this root may carry a password"


# --- egress and account policies ----------------------------------------------------------------------


def test_hub_egress_is_off_by_default():
    assert "default     = false" in hcl_block(source("variables.tf"), 'variable "enable_hub_egress"')


def test_the_egress_integration_exists_only_when_enabled():
    text = source("network.tf")
    for header in (
        'resource "snowflake_network_rule" "hub_egress"',
        'resource "snowflake_external_access_integration" "hub"',
    ):
        block = hcl_block(text, header)
        assert "count = var.enable_hub_egress ? 1 : 0" in block, f"{header} must be gated on enable_hub_egress"


def test_egress_is_limited_to_the_hub_and_the_fl_endpoints():
    block = hcl_block(source("network.tf"), 'resource "snowflake_network_rule" "hub_egress"')
    assert 'type       = "HOST_PORT"' in block, "egress is named hosts and ports, never an address range"
    assert "value_list = local.egress_hosts" in block
    locals_text = source("locals.tf")
    assert "var.hub_api_host}:443" in locals_text, "the hub is reachable on 443 only"
    assert "var.fl_server_endpoints" in locals_text
    assert "0.0.0.0" not in locals_text, "no catch-all egress"


def test_the_integration_allows_no_authentication_secrets():
    block = hcl_block(source("network.tf"), 'resource "snowflake_external_access_integration" "hub"')
    assert "none = true" in block, "secrets are bound to services by sync-kit, not through the integration"


def test_the_eai_is_usable_by_the_deploy_and_service_roles():
    """A service spec can only reference an integration its role holds USAGE on."""
    for name in ("deploy_eai", "svc_eai"):
        block = hcl_block(source("grants.tf"), f'resource "snowflake_grant_privileges_to_account_role" "{name}"')
        assert block, name
        assert 'object_type = "INTEGRATION"' in block
        assert 'privileges        = ["USAGE"]' in block


def test_the_account_policies_live_here():
    for resource_type in (
        "snowflake_password_policy",
        "snowflake_session_policy",
        "snowflake_account_password_policy_attachment",
        "snowflake_account_session_policy_attachment",
    ):
        assert resource_blocks(all_terraform(), resource_type), resource_type
    assert resource_blocks(source("network.tf"), "snowflake_network_policy_attachment")


# --- observability and cost -----------------------------------------------------------------------


def test_the_event_table_lives_in_a_platform_database():
    locals_text = source("locals.tf")
    assert 'platform_database = "FLIP_PLATFORM"' in locals_text
    assert 'obs      = "OBS"' in locals_text
    assert resource_blocks(source("platform_database.tf"), "snowflake_database"), (
        "an event table needs a database, and it must not be the trust database"
    )
    assert "FLIP_TRUST_" not in source("observability.tf"), (
        "the event table must not live in a database the workload root can destroy"
    )


def test_the_account_parameter_points_at_that_event_table():
    block = hcl_block(source("observability.tf"), 'resource "snowflake_account_parameter" "event_table"')
    assert block, "EVENT_TABLE is an account parameter, so it belongs to the break-glass tier"
    assert 'key   = "EVENT_TABLE"' in block
    assert "value = local.event_table_fqn" in block


def test_the_observer_role_can_read_the_event_table():
    block = hcl_block(source("grants.tf"), 'resource "snowflake_execute" "observer_event_table"')
    assert block, "the observer role must be granted SELECT on the event table"
    assert "GRANT SELECT ON EVENT TABLE" in block
    assert "REVOKE SELECT ON EVENT TABLE" in block


def test_only_the_observer_and_deploy_roles_can_read_the_logs():
    """The event table holds every service's logs, so a running service must not read it.

    SPCS services run as FLIP_<CODE>_SVC, which may inherit OMOP_READER and nothing else. OBSERVER,
    which holds SELECT on the event table, is granted to the deploy role only (so an operator
    deploying can read logs, as the first live spike did) and never into the service hierarchy.
    """
    grants = resource_blocks(all_terraform(), "snowflake_grant_account_role")
    into_svc = [b for b in grants if "parent_role_name = snowflake_account_role.svc.name" in b]
    assert len(into_svc) == 1, "SVC must inherit exactly one role"
    assert "role_name        = snowflake_account_role.omop_reader.name" in into_svc[0]

    for block in grants:
        if "snowflake_account_role.observer.name" in block and "for_each" not in block:
            assert "parent_role_name = snowflake_account_role.deploy.name" in block, block
    for block in grants:
        if "snowflake_account_role.converter.name" in block and "for_each" not in block:
            raise AssertionError("CONVERTER (CREATE SERVICE) is reachable only through the deploy role")
    event_table_grants = re.findall(r"GRANT SELECT ON EVENT TABLE \S+ TO ROLE \$\{([^}]+)\}", all_terraform())
    assert event_table_grants == ["snowflake_account_role.observer.name"]


def test_a_resource_monitor_caps_warehouse_credits():
    block = hcl_block(source("cost.tf"), 'resource "snowflake_resource_monitor" "trust"')
    assert "credit_quota = var.credit_quota" in block
    assert "suspend_immediate_trigger = 100" in block


def test_the_warehouse_is_platform_owned_with_the_monitor_attached():
    """Assigning a resource monitor needs ACCOUNTADMIN, so the warehouse lives here. The deploy role
    may use and operate it but not MODIFY or own it, so the deploy key cannot detach the monitor."""
    block = hcl_block(source("warehouse.tf"), 'resource "snowflake_warehouse" "trust"')
    assert "resource_monitor = snowflake_resource_monitor.trust.name" in block
    header = 'resource "snowflake_grant_privileges_to_account_role" "deploy_warehouse"'
    grant = hcl_block(source("warehouse.tf"), header)
    assert 'privileges        = ["USAGE", "OPERATE", "MONITOR"]' in grant
    assert "MODIFY" not in grant
    assert "OWNERSHIP" not in grant
    assert "deploy_resource_monitor" not in source("grants.tf"), "the deploy role needs no MODIFY on the monitor"


# --- account hygiene --------------------------------------------------------------------------------


def test_no_account_identifier_is_committed():
    for name in ("organization_name", "account_name"):
        block = hcl_block(source("variables.tf"), f'variable "{name}"')
        assert "default" not in block, f"{name} must be supplied at apply time, never defaulted"
    # Git, not the disk: an operator's own gitignored terraform.<env>.tfvars is exactly what the
    # README asks for, and must not fail the suite on their machine.
    tracked = subprocess.run(
        ["git", "ls-files", "--", "*.tfvars"], cwd=ROOT_DIR, capture_output=True, text=True, check=True
    ).stdout.split()
    assert not tracked, f"a real tfvars file must never be tracked: {tracked}"


def test_gitignore_hides_state_tfvars_and_the_encryption_block():
    ignored = (ROOT_DIR / ".gitignore").read_text()
    for pattern in ("*.tfstate", "*.tfvars", "encryption.tf", "backend_override.tf"):
        assert pattern in ignored, pattern


def test_the_backend_is_a_partial_configuration():
    assert 'backend "local" {}' in hcl_block(source("backend.tf"), "terraform {")


def test_the_two_roots_keep_separate_state():
    makefile = (ROOT_DIR / "Makefile").read_text()
    assert "terraform-platform.$(ENV).tfstate" in makefile, (
        "the platform state file must not collide with the workload one"
    )


def test_the_makefile_shouts_and_confirms():
    makefile = (ROOT_DIR / "Makefile").read_text()
    assert "BREAK GLASS" in makefile, "the operator must be told what tier this is"
    assert 'read -r -p "Type BREAK GLASS to continue: " answer' in makefile, (
        "apply and destroy need an interactive confirmation"
    )
    for target in ("apply", "destroy"):
        block = re.search(rf"^{target}:.*?(?=^\S)", makefile, re.M | re.S).group(0)
        assert "break_glass_warning" in block, f"{target} must print the warning"
    assert "auto-approve" not in makefile, "apply and destroy must ask"


@pytest.mark.parametrize("target", ["init", "plan", "apply", "destroy", "lint", "test"])
def test_makefile_has_target(target):
    assert re.search(rf"^{target}:", (ROOT_DIR / "Makefile").read_text(), re.M), target


def test_no_dash_typography_in_prose():
    """House style: no em dash or en dash in prose, including comments and the README."""
    for path in [*TF_FILES, ROOT_DIR / "README.md", ROOT_DIR / "Makefile", DASHBOARD_SQL, DASHBOARD_README]:
        text = path.read_text()
        assert "\u2014" not in text, f"{path.name} uses an em dash"
        assert "\u2013" not in text, f"{path.name} uses an en dash"


def test_the_operator_identity_is_never_managed_by_this_root() -> None:
    """The just-in-time operator is the identity this root runs AS. If the root could create,
    alter or grant to it, an apply could keep its own privilege or lock the operator out. It is
    created once by hand and granted its role only by an MFA-protected admin in Snowsight."""
    for path in sorted(ROOT_DIR.glob("*.tf")):
        code = "\n".join(line for line in path.read_text().splitlines() if not line.lstrip().startswith("#"))
        assert "FLIP_PLATFORM_OPERATOR" not in code, path.name


def test_apply_and_destroy_always_print_the_revoke() -> None:
    makefile = (ROOT_DIR / "Makefile").read_text()
    for target in ("apply:", "destroy:"):
        body = makefile.split(target, 1)[1].split("\n\n", 1)[0]
        assert "REVOKE ROLE" in body, f"{target} must remind the operator to revoke the role"


# --- observability: views, retention, dashboard (FLIP#1584) ---------------------------------------
#
# The whole of #1584 lives in this tier, and the reason is one sentence: a view runs with its
# OWNER's rights, so a view over SNOWFLAKE.ACCOUNT_USAGE can only be owned by a role holding
# IMPORTED PRIVILEGES on database SNOWFLAKE, which the deploy role must never hold. Owning them
# here also keeps a workload `make destroy` from taking the account's observability with it.

VIEW_RESOURCES = (
    "events",
    "service_status",
    "service_restarts",
    "recent_errors",
    "fl_client_logs",
    "compute_pool_cost",
    "warehouse_cost",
)

VIEW_NAMES = (
    "V_EVENTS",
    "V_SERVICE_STATUS",
    "V_SERVICE_RESTARTS",
    "V_RECENT_ERRORS",
    "V_FL_CLIENT_LOGS",
    "V_COMPUTE_POOL_COST",
    "V_WAREHOUSE_COST",
)


def _view_blocks() -> list[str]:
    return resource_blocks(source("observability_views.tf"), "snowflake_view")


@pytest.mark.parametrize("name", VIEW_NAMES)
def test_the_contracts_observability_views_exist(name):
    assert f'{name}"' in source("locals.tf"), f"{name} is missing from local.obs_views"


@pytest.mark.parametrize("resource_name", VIEW_RESOURCES)
def test_every_view_is_declared(resource_name):
    assert hcl_block(source("observability_views.tf"), f'resource "snowflake_view" "{resource_name}"'), resource_name


def test_every_view_lives_in_the_platform_obs_schema():
    blocks = _view_blocks()
    assert len(blocks) == len(VIEW_RESOURCES), "one resource per view, so each can be named in a grant"
    for block in blocks:
        assert "database    = snowflake_database.platform.name" in block, block[:80]
        assert "schema      = local.platform_schemas.obs" in block, block[:80]
        assert "name        = local.obs_views." in block, "a view name comes from the contract in locals.tf"
    code = "\n".join(
        line for line in source("observability_views.tf").splitlines() if not line.lstrip().startswith("#")
    )
    assert "FLIP_TRUST_" not in code, "the views must not live in a database the workload root can destroy"


def test_every_view_copies_its_grants_on_replace():
    """Changing a statement replaces the view; without copy_grants the observer loses SELECT
    until the next apply of the grant, which is exactly when somebody is reading logs."""
    for block in _view_blocks():
        assert "copy_grants = true" in block, block[:80]


def test_the_base_view_orders_the_others():
    """Each derived view reads the base view by reference, so the provider creates them in order
    rather than failing on a view that does not exist yet."""
    text = source("observability_views.tf")
    for resource_name in VIEW_RESOURCES:
        if resource_name in ("events", "compute_pool_cost", "warehouse_cost"):
            continue
        block = hcl_block(text, f'resource "snowflake_view" "{resource_name}"')
        assert "snowflake_view.events.fully_qualified_name" in block, resource_name
    events = hcl_block(text, 'resource "snowflake_view" "events"')
    assert "local.event_table_fqn" in events
    assert "snowflake_execute.event_table" in events, "the base view cannot be created before the event table"


def test_the_cost_views_read_account_usage():
    for resource_name, table in (
        ("compute_pool_cost", "SNOWPARK_CONTAINER_SERVICES_HISTORY"),
        ("warehouse_cost", "WAREHOUSE_METERING_HISTORY"),
    ):
        block = hcl_block(source("observability_views.tf"), f'resource "snowflake_view" "{resource_name}"')
        assert f"SNOWFLAKE.ACCOUNT_USAGE.{table}" in block, resource_name
        assert "var.cost_window_days" in block, "a cost view must be bounded, not a full history scan"


def test_the_observer_reads_account_usage_only_through_the_views():
    """The observer holds SELECT on the views and no account-level route to the same data. An
    IMPORTED PRIVILEGES grant, or a SNOWFLAKE database role, would let any holder of the role read
    every query text in the account, including the OMOP queries a cohort is built from."""
    text = all_terraform()
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "IMPORTED PRIVILEGES" not in code, "the observer reads ACCOUNT_USAGE through owner's-rights views"
    assert not resource_blocks(text, "snowflake_grant_database_role"), (
        "no SNOWFLAKE database role (USAGE_VIEWER, GOVERNANCE_VIEWER, ...) is granted to a FLIP role"
    )


def test_only_the_observer_can_select_the_obs_views():
    for name in ("observer_obs_views", "observer_future_obs_views"):
        block = hcl_block(source("grants.tf"), f'resource "snowflake_grant_privileges_to_account_role" "{name}"')
        assert block, name
        assert "account_role_name = snowflake_account_role.observer.name" in block, name
        assert 'privileges        = ["SELECT"]' in block, name
        assert 'object_type_plural = "VIEWS"' in block, name
    grants = resource_blocks(all_terraform(), "snowflake_grant_privileges_to_account_role")
    for block in [b for b in grants if '"SELECT"' in b]:
        assert "snowflake_account_role.observer.name" in block, (
            f"only the observer may SELECT in the platform tier:\n{block}"
        )


def test_the_service_role_cannot_reach_the_platform_database():
    """A container must not read its neighbours' logs. SVC has no USAGE on FLIP_PLATFORM, so the
    views are out of reach whatever is granted inside the schema."""
    for block in resource_blocks(all_terraform(), "snowflake_grant_privileges_to_account_role"):
        if "snowflake_database.platform.name" in block:
            assert "snowflake_account_role.svc.name" not in block, block


# --- retention ------------------------------------------------------------------------------------


def test_the_event_table_has_a_retention_task():
    """Snowflake expires nothing in an event table by itself: DATA_RETENTION_TIME_IN_DAYS there is
    Time Travel. The retention policy is the DELETE, and it is platform-owned so the deploy key
    can neither lengthen the window nor switch it off."""
    block = hcl_block(source("observability.tf"), 'resource "snowflake_task" "prune_events"')
    assert block, "the event table would grow forever without a pruning task"
    assert "database  = snowflake_database.platform.name" in block
    assert "DELETE FROM ${local.event_table_fqn}" in block
    assert "var.event_table_retention_days" in block
    assert "started   = true" in block, "a task that is not started is not a retention policy"


def test_the_retention_window_is_bounded():
    block = hcl_block(source("variables.tf"), 'variable "event_table_retention_days"')
    assert "var.event_table_retention_days <= 90" in block, "logs are operational data, not a record"
    assert "var.event_table_retention_days >= 1" in block


def test_the_event_tables_time_travel_stays_short():
    """Pruned rows must not be kept for another week in Time Travel storage."""
    text = source("observability.tf")
    assert "DATA_RETENTION_TIME_IN_DAYS = 1" in text
    assert hcl_block(text, 'resource "snowflake_execute" "event_table_time_travel"'), (
        "the CREATE is IF NOT EXISTS, so an account whose event table predates this needs the ALTER"
    )


def test_verbosity_is_per_service_and_never_an_account_parameter():
    """A LOG_LEVEL parameter would collect Snowflake-internal output account-wide and bill its
    storage. Each spec sets its own logExporters.eventTableConfig.logLevel instead."""
    for root in (ROOT_DIR, ROOT_DIR.parent / "snowflake"):
        for path in sorted(root.glob("*.tf")):
            assert '"LOG_LEVEL"' not in path.read_text(), f"{root.name}/{path.name} sets a LOG_LEVEL parameter"
    for block in resource_blocks(all_terraform(), "snowflake_account_parameter"):
        assert 'key   = "EVENT_TABLE"' in block, "EVENT_TABLE is the only account parameter this root sets"


# --- the dashboard ----------------------------------------------------------------------------------


def test_the_dashboard_is_in_the_repo():
    assert DASHBOARD_SQL.is_file(), "Snowsight has no API, so the dashboard is kept as the SQL of its tiles"
    assert DASHBOARD_README.is_file(), "and a README saying how to load it"
    assert "Licensed under the Apache License, Version 2.0" in DASHBOARD_SQL.read_text()
    assert "Licensed under the Apache License, Version 2.0" in DASHBOARD_README.read_text()


def test_the_dashboard_runs_as_the_observer_role():
    sql = DASHBOARD_SQL.read_text()
    assert "USE ROLE FLIP_<CODE>_OBSERVER;" in sql, "the dashboard is granted to the observer role, not to a person"
    assert "ACCOUNTADMIN" not in sql, "no tile may need the break-glass role"


@pytest.mark.parametrize("name", VIEW_NAMES)
def test_the_dashboard_covers_every_view(name):
    assert f"FLIP_PLATFORM.OBS.{name}" in DASHBOARD_SQL.read_text(), f"no tile reads {name}"


def test_the_dashboard_reads_nothing_but_the_views():
    """A tile that queried the event table or ACCOUNT_USAGE directly would need privileges the
    observer deliberately does not have, so it would fail only when somebody opened it."""
    sql = DASHBOARD_SQL.read_text()
    code = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    assert "ACCOUNT_USAGE" not in code
    assert "FLIP_EVENTS" not in code
    read = set(re.findall(r"FLIP_PLATFORM\.OBS\.(V_[A-Z_]+)", code))
    assert read == set(VIEW_NAMES), f"the dashboard reads {sorted(read)}, the views are {sorted(VIEW_NAMES)}"


def test_the_grafana_follow_up_is_recorded_and_not_half_built():
    readme = (ROOT_DIR / "README.md").read_text()
    assert "Grafana" in readme, "the Grafana-on-SPCS follow-up is a note in Open points, nothing more"
    assert "grafana" not in all_terraform().lower(), "nothing about Grafana is built in this issue"
