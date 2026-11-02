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
"""Static guards for the Snowflake BOOTSTRAP root (epic #1589, step 2).

The only root applied with ACCOUNTADMIN. It holds what nothing else may: the resource monitor and
the warehouse it caps, the account parameter and policies, the egress integration, and the CI role
and users that apply the platform root. Most of these guards are about the CI role: what it may do
is exactly what this file allows, so the list is pinned and every widening fails a test.
"""

from __future__ import annotations

import re

import pytest
from conftest import ROOT_DIR, TF_FILES, all_terraform, source
from tf_blocks import hcl_block, resource_blocks

PLATFORM_DIR = ROOT_DIR.parent / "snowflake-platform"

ROOT_FILES = (
    "versions.tf",
    "backend.tf",
    "providers.tf",
    "variables.tf",
    "locals.tf",
    "admin_database.tf",
    "ci.tf",
    "cost.tf",
    "warehouse.tf",
    "network.tf",
    "policies.tf",
    "observability.tf",
    "outputs.tf",
)

# Exactly the account privileges the CI role holds outright, and the ones it may pass on.
CI_ACCOUNT_PRIVILEGES = {"CREATE ROLE", "CREATE USER", "CREATE DATABASE", "EXECUTE TASK"}
CI_GRANTABLE_ACCOUNT_PRIVILEGES = {"CREATE DATABASE", "CREATE COMPUTE POOL", "BIND SERVICE ENDPOINT", "EXECUTE TASK"}

# What would let CI out of its box.
NEVER_FOR_CI = (
    "MANAGE GRANTS",
    "CREATE INTEGRATION",
    "CREATE NETWORK POLICY",
    "CREATE ACCOUNT",
    "CREATE SHARE",
    "APPLY MASKING POLICY",
    "APPLY ROW ACCESS POLICY",
    "APPLY PASSWORD POLICY",
    "APPLY SESSION POLICY",
    "MANAGE ACCOUNT SUPPORT CASES",
    "OWNERSHIP",
)
SYSTEM_ROLES = ("ACCOUNTADMIN", "SECURITYADMIN", "USERADMIN", "ORGADMIN")

# The only resource types the platform root may declare, so everything it needs is within reach of
# FLIP_PLATFORM_CI. Anything account-level belongs here instead.
PLATFORM_ALLOWED_TYPES = {
    "snowflake_account_role",
    "snowflake_grant_account_role",
    "snowflake_grant_privileges_to_account_role",
    "snowflake_service_user",
    "snowflake_database",
    "snowflake_schema",
    "snowflake_execute",
    "snowflake_task",
    "snowflake_view",
}


def _list(text: str, local: str) -> set[str]:
    match = re.search(rf"{local}\s*=\s*\[(.*?)\]", text, re.S)
    assert match, local
    return set(re.findall(r'"([^"]+)"', match.group(1)))


@pytest.mark.parametrize("name", ROOT_FILES)
def test_the_root_has_its_files(name):
    assert (ROOT_DIR / name).is_file(), name


@pytest.mark.parametrize("path", TF_FILES, ids=lambda p: p.name)
def test_apache_header(path):
    assert "Licensed under the Apache License, Version 2.0" in path.read_text().split("\n\n", 1)[0]


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
        block = hcl_block(source("observability.tf"), f'resource "snowflake_grant_privileges_to_account_role" "{name}"')
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


def test_the_account_parameter_points_at_that_event_table():
    block = hcl_block(source("observability.tf"), 'resource "snowflake_account_parameter" "event_table"')
    assert block, "EVENT_TABLE is an account parameter, so it belongs to the bootstrap tier"
    assert "count = var.set_event_table ? 1 : 0" in block, "set only once the platform root made the table"
    assert 'key   = "EVENT_TABLE"' in block
    assert "value = local.event_table_fqn" in block


def test_a_resource_monitor_caps_warehouse_credits():
    block = hcl_block(source("cost.tf"), 'resource "snowflake_resource_monitor" "trust"')
    assert "credit_quota = var.credit_quota" in block
    assert "suspend_immediate_trigger = 100" in block


def test_the_warehouse_carries_the_monitor_and_ci_may_only_pass_on_its_use():
    """Assigning a resource monitor needs ACCOUNTADMIN, so the warehouse lives here. CI holds use,
    operate and monitor with the grant option (to give the trust roles), never MODIFY or OWNERSHIP,
    so nothing CI runs can detach the monitor."""
    block = hcl_block(source("warehouse.tf"), 'resource "snowflake_warehouse" "trust"')
    assert "resource_monitor = snowflake_resource_monitor.trust.name" in block
    grant = hcl_block(source("warehouse.tf"), 'resource "snowflake_grant_privileges_to_account_role" "ci_warehouse"')
    assert "privileges        = local.ci_warehouse_privileges" in grant
    assert "with_grant_option = true" in grant
    assert 'ci_warehouse_privileges = ["USAGE", "OPERATE", "MONITOR"]' in source("locals.tf")
    for word in ("MODIFY", "OWNERSHIP"):
        assert word not in source("warehouse.tf").split('resource "snowflake_grant_privileges_to_account_role"')[1]


def test_the_ci_role_holds_exactly_its_account_privileges():
    assert _list(source("locals.tf"), "ci_account_privileges") == CI_ACCOUNT_PRIVILEGES
    block = hcl_block(source("ci.tf"), 'resource "snowflake_grant_privileges_to_account_role" "ci_account"')
    assert "privileges        = local.ci_account_privileges" in block
    assert "with_grant_option" not in block, "CI may not pass on CREATE ROLE / CREATE USER"


def test_the_ci_role_may_pass_on_only_the_deploy_roles_privileges():
    assert _list(source("locals.tf"), "deploy_account_privileges") == CI_GRANTABLE_ACCOUNT_PRIVILEGES
    platform_locals = (PLATFORM_DIR / "locals.tf").read_text()
    assert _list(platform_locals, "deploy_account_privileges") == CI_GRANTABLE_ACCOUNT_PRIVILEGES, (
        "the platform root grants DEPLOY exactly what CI holds with the grant option, or its apply is refused"
    )


def test_the_grant_option_appears_only_where_ci_passes_privileges_on():
    with_option = {
        name
        for name in re.findall(r'resource "snowflake_grant_privileges_to_account_role" "(\w+)"', all_terraform())
        if "with_grant_option = true"
        in hcl_block(all_terraform(), f'resource "snowflake_grant_privileges_to_account_role" "{name}"')
    }
    assert with_option == {"ci_account_grantable", "ci_warehouse"}


@pytest.mark.parametrize("privilege", NEVER_FOR_CI)
def test_the_ci_role_never_gets(privilege):
    ci = source("ci.tf") + source("locals.tf") + source("warehouse.tf")
    code = "\n".join(line for line in ci.splitlines() if not line.lstrip().startswith("#"))
    assert f'"{privilege}"' not in code


def test_the_ci_role_holds_no_system_role_and_sits_under_sysadmin_only():
    grants = resource_blocks(all_terraform(), "snowflake_grant_account_role")
    for block in grants:
        if "snowflake_account_role.platform_ci" in block.split("role_name", 1)[1].split("\n", 1)[0]:
            assert 'parent_role_name = "SYSADMIN"' in block or "user_name" in block
        for role in SYSTEM_ROLES:
            assert f'role_name = "{role}"' not in block, role
            assert f'role_name        = "{role}"' not in block, role


def test_the_ci_users_log_in_with_github_oidc_only():
    block = hcl_block(source("ci.tf"), 'resource "snowflake_service_user" "ci"')
    assert 'issuer  = "https://token.actions.githubusercontent.com"' in block
    for forbidden in ("rsa_public_key", "password", "login_name"):
        assert forbidden not in block, forbidden
    assert 'github_oidc_subject = "repo:${var.github_repository}:environment:${var.ci_github_environment}"' in source(
        "locals.tf"
    ), "the environment form of the subject: a ref form would let any branch push in"
    assert (
        'github_oidc_plan_subject = "repo:${var.github_repository}:environment:${var.ci_github_environment}-plan"'
        in source("locals.tf")
    )
    assert (
        'plan  = { name = "FLIP_CI_PLAN_USER", role = local.ci_read_role, subject = local.github_oidc_plan_subject }'
        in source("locals.tf")
    ), "one OIDC subject maps to one Snowflake user: plan and apply need different environments"
    assert 'apply = { name = "FLIP_CI_APPLY_USER", role = local.ci_role' in source("locals.tf")


def test_the_ci_environment_is_one_of_the_two():
    block = hcl_block(source("variables.tf"), 'variable "ci_github_environment"')
    assert 'contains(["sf-stag", "sf-prod"], var.ci_github_environment)' in block


def test_the_account_policies_are_outside_the_ci_owned_database():
    for name in ("policies.tf", "network.tf"):
        assert "snowflake_database.platform" not in source(name)
        assert "snowflake_database.admin.name" in source(name)


def test_the_platform_root_declares_only_what_ci_may_manage():
    platform = "\n".join(p.read_text() for p in sorted(PLATFORM_DIR.glob("*.tf")))
    declared = set(re.findall(r'^resource "(\w+)"', platform, re.M))
    assert declared <= PLATFORM_ALLOWED_TYPES, declared - PLATFORM_ALLOWED_TYPES
    assert "with_grant_option" not in platform
    assert "FLIP_ADMIN" not in platform
