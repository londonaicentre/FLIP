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
"""Static guards for the Snowflake WORKLOAD root (FLIP#1581, epic #1589).

The least-privilege tier, applied routinely as FLIP_<CODE>_DEPLOY_USER. These are the invariants
no runtime test can see without a Snowflake account, and most of them are about what this root
must NOT contain: no role, no user, no integration, no account policy, no account parameter and no
account-level grant. Everything in that list belongs to ../snowflake-platform, the break-glass
tier. Plus the posture guards: nothing to PUBLIC, SSE on every stage, auto-suspend on every pool,
and no secret value or password field in Terraform.
"""

from __future__ import annotations

import re
import subprocess

import pytest
from conftest import PLATFORM_DIR, SNOWFLAKE_DIR, TF_FILES, all_terraform, source
from tf_blocks import hcl_block, resource_blocks

ROOT_FILES = (
    "versions.tf",
    "backend.tf",
    "providers.tf",
    "variables.tf",
    "locals.tf",
    "database.tf",
    "compute_pools.tf",
    "image_repository.tf",
    "stages.tf",
    "cost.tf",
    "grants.tf",
    "converter.tf",
    "outputs.tf",
)

# Resource types that would put an account-level security object back in the routine apply.
FORBIDDEN_RESOURCES = (
    "snowflake_account_role",
    "snowflake_user",
    "snowflake_service_user",
    "snowflake_legacy_service_user",
    "snowflake_grant_account_role",
    "snowflake_external_access_integration",
    "snowflake_api_integration",
    "snowflake_notification_integration",
    "snowflake_storage_integration",
    "snowflake_network_rule",
    "snowflake_network_policy",
    "snowflake_network_policy_attachment",
    "snowflake_password_policy",
    "snowflake_session_policy",
    "snowflake_account_password_policy_attachment",
    "snowflake_account_session_policy_attachment",
    "snowflake_account_parameter",
    "snowflake_resource_monitor",
)

# The stages the naming contract (#1589) requires, each mounted by a different service.
STAGES = ("FL_KIT", "SEED", "XNAT_ARCHIVE", "IMAGES", "CONVERTER_IO", "GOVERNANCE", "COHORT_SNAPSHOTS")

# Kit values that `make sync-kit` writes into Snowflake SECRET objects (#1582). None of them may
# appear as a Terraform variable, local or literal here.
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
    assert (SNOWFLAKE_DIR / name).is_file(), name


@pytest.mark.parametrize("path", TF_FILES, ids=lambda p: p.name)
def test_apache_header(path):
    assert "Licensed under the Apache License, Version 2.0" in path.read_text(), path.name


def test_the_platform_root_exists_beside_this_one():
    assert (PLATFORM_DIR / "roles.tf").is_file(), (
        "the account's roles, user, integration, policies and account grants live in ../snowflake-platform"
    )


# --- what this tier may not contain ----------------------------------------------------------------


@pytest.mark.parametrize("resource_type", FORBIDDEN_RESOURCES)
def test_no_account_level_security_resource(resource_type):
    assert not resource_blocks(all_terraform(), resource_type), (
        f"{resource_type} belongs to the break-glass platform root, not to the routine apply"
    )


def test_no_account_level_grant():
    for block in resource_blocks(source("grants.tf"), "snowflake_grant_privileges_to_account_role"):
        assert not re.search(r"^\s*on_account\s*=\s*true", block, re.M), (
            "an account privilege is granted once, by the platform root. This root grants only on objects it owns:"
            f"\n{block}"
        )


@pytest.mark.parametrize(
    "privilege",
    [
        "CREATE ROLE",
        "CREATE USER",
        "CREATE INTEGRATION",
        "CREATE NETWORK POLICY",
        "MANAGE GRANTS",
        "CREATE DATABASE",
        "CREATE WAREHOUSE",
        "CREATE COMPUTE POOL",
        "BIND SERVICE ENDPOINT",
        "EXECUTE TASK",
    ],
)
def test_no_account_privilege_is_named_here(privilege):
    """Including the ones the deploy role does hold: they are granted in the platform root only."""
    for path in TF_FILES:
        assert f'"{privilege}"' not in path.read_text(), f"{path.name} names the account privilege {privilege}"


def test_the_admin_role_is_retired():
    text = re.sub(r"#.*", "", all_terraform())
    assert "_ADMIN" not in text, "FLIP_<CODE>_ADMIN is retired (two-tier decision, epic #1589)"


def test_the_root_is_applied_as_the_deploy_role():
    block = hcl_block(source("variables.tf"), 'variable "terraform_role"')
    assert "FLIP_[A-Z0-9]+_DEPLOY" in block, "the role is validated, so a privileged role cannot be passed by mistake"
    assert "default" not in block, "the role is supplied at apply time"


def test_accountadmin_is_never_a_configured_value():
    for path in TF_FILES:
        assert not re.search(r'=\s*"(ACCOUNTADMIN|SECURITYADMIN|SYSADMIN)"', path.read_text()), (
            f"{path.name}: privileged roles belong to the platform root"
        )


# --- grants ---------------------------------------------------------------------------------------


def test_nothing_is_granted_to_public():
    text = all_terraform()
    assert not re.search(r'account_role_name\s*=\s*"PUBLIC"', text), "PUBLIC must never receive a grant"
    assert "PUBLIC" not in re.sub(r"#.*", "", text).replace("drop_public_schema_on_creation", ""), (
        "the only mention of PUBLIC may be dropping the database's PUBLIC schema"
    )


def test_every_grant_names_a_flip_role_by_convention():
    """The roles are not resources here: the platform tier owns them, this root names them."""
    blocks = [
        *resource_blocks(all_terraform(), "snowflake_grant_privileges_to_account_role"),
        *resource_blocks(all_terraform(), "snowflake_grant_ownership"),
    ]
    for block in blocks:
        match = re.search(r"account_role_name\s*=\s*([^\n]+)", block)
        assert match, block
        assert match.group(1).strip().startswith("local.roles."), (
            f"a grant must name one of the contract's roles, not {match.group(1)!r}"
        )


def test_no_grant_uses_all_privileges_or_grant_option():
    text = all_terraform()
    assert "all_privileges" not in text, "least privilege: list the privileges"
    assert "with_grant_option" not in text, "no role may pass its privileges on"


def test_the_deploy_role_grants_itself_nothing():
    """It owns every object this root creates; an owner needs no grant."""
    assert "local.roles.deploy" not in all_terraform().split("roles = {")[-1].split("}", 1)[-1]


# --- the converter launch path (FLIP#1578) ------------------------------------------------------
#
# A service's token carries the role that OWNS it, and SPCS cannot transfer a service's ownership.
# So CREATE SERVICE decides whose token a container gets, and it must sit only with the role that
# owns the converter procedures. The services role never holds it at rest (#1582 grants it for the
# seconds of a deploy and revokes it); imaging-api reaches the converter through CALL only.


def _procedures() -> list[str]:
    return resource_blocks(source("converter.tf"), "snowflake_procedure_sql")


def test_create_service_is_held_at_rest_only_by_the_converter_role():
    holders = [
        block
        for block in resource_blocks(all_terraform(), "snowflake_grant_privileges_to_account_role")
        if '"CREATE SERVICE"' in block
    ]
    assert len(holders) == 1, "exactly one role holds CREATE SERVICE through Terraform"
    assert "account_role_name = local.roles.converter" in holders[0]


def test_the_services_role_may_only_call_the_converter_procedures():
    text = all_terraform()
    for block in resource_blocks(text, "snowflake_grant_privileges_to_account_role"):
        if "account_role_name = local.roles.svc" in block and '"PROCEDURE"' in block:
            assert 'privileges        = ["USAGE"]' in block, block
    assert "svc_converter_procedures" in text, "imaging-api (as SVC) must be able to CALL CONVERT_SCAN"


def test_the_converter_procedures_run_with_the_converter_roles_rights():
    procedures = _procedures()
    assert {re.search(r'name\s+= "(\w+)"', b).group(1) for b in procedures} == {"CONVERT_SCAN", "CONVERT_STATUS"}
    for block in procedures:
        assert 'execute_as  = "OWNER"' in block, block[:80]
    ownership = hcl_block(source("converter.tf"), 'resource "snowflake_grant_ownership" "converter_procedures"')
    assert "account_role_name   = local.roles.converter" in ownership
    for name in ("convert_scan", "convert_status"):
        assert f"snowflake_procedure_sql.{name}.fully_qualified_name" in ownership, name


def test_converter_arguments_are_checked_before_any_dynamic_sql():
    """The identifiers become a stage path, a shell argument and part of EXECUTE IMMEDIATE text."""
    guard = hcl_block(source("converter.tf"), "locals {")
    patterns = re.findall(r"REGEXP_LIKE\(:(\w+), '([^']+)'\)", guard)
    assert {name for name, _ in patterns} == {"PROJECT", "SESSION", "SCAN"}
    for _, pattern in patterns:
        assert pattern.startswith("^[A-Za-z0-9_-]{1,"), pattern
        assert pattern.endswith("}$"), pattern
    assert "RETURN 'rejected';" in guard
    for block in _procedures():
        body = block[block.index("procedure_definition") :]
        assert "local.converter_args_ok" in body, block[:80]
        first_dynamic = body.find("EXECUTE IMMEDIATE")
        first_status = body.find("GET_SERVICE_STATUS")
        first_use = min(i for i in (first_dynamic, first_status) if i >= 0)
        assert body.index("local.converter_args_ok") < first_use, "validate before use"


def test_the_converter_spec_is_fixed_and_cannot_close_the_procedure_body():
    """Image, pool, command and mounts are written here, never passed in; and a $$ in the body would
    end the provider's $$-quoted procedure definition."""
    scan = hcl_block(source("converter.tf"), 'resource "snowflake_procedure_sql" "convert_scan"')
    assert "$$" not in scan
    assert "image: ${local.converter_image}" in scan
    assert "IN COMPUTE POOL ${local.job_pool}" in scan
    assert "ASYNC = TRUE" in scan, "a converter run must not hold the caller's statement open"
    assert scan.count("name: input") == 2
    assert scan.count("name: output") == 2


def test_the_converter_mounts_one_session_and_reads_the_archive_only():
    scan = hcl_block(source("converter.tf"), 'resource "snowflake_procedure_sql" "convert_scan"')
    assert "@${local.archive_stage}/' || :PROJECT || '/arc001/' || :SESSION || '\"" in scan, (
        "stage volumes cannot be read-only, so the job mounts one session's subpath, not the archive"
    )
    archive = hcl_block(
        source("converter.tf"), 'resource "snowflake_grant_privileges_to_account_role" "converter_archive_read"'
    )
    assert 'privileges        = ["READ"]' in archive


def test_the_converter_image_follows_the_dcm2niix_pin():
    text = source("converter.tf")
    assert 'converter_source_image = "ghcr.io/londonaicentre/xnat-dcm2niix:' in text
    pin_check = (SNOWFLAKE_DIR.parents[3] / "trust/xnat/dcm2niix/check_image_pin_sync.sh").read_text()
    assert '"trust/deploy/terraform/snowflake/converter.tf"' in pin_check


# --- secrets ------------------------------------------------------------------------------------


@pytest.mark.parametrize("secret", SECRET_NAMES)
def test_no_kit_secret_is_named_by_terraform(secret):
    for path in TF_FILES:
        assert secret not in path.read_text(), f"{path.name} names {secret}: secrets are written by make sync-kit"


def test_no_secret_resource_and_no_password_field():
    text = all_terraform()
    assert "snowflake_secret_with" not in text, "Terraform must create no SECRET: it would hold the value in state"
    assert not re.search(r"^\s*password\s*=", text, re.M), "no resource in this root may carry a password"
    assert "private_key" not in text, "the deploy key never reaches Terraform"


def test_secret_privileges_are_declared_as_future_grants():
    block = hcl_block(source("grants.tf"), 'resource "snowflake_grant_privileges_to_account_role" "svc_future_secrets"')
    assert block, "the service role needs a future grant covering the secrets sync-kit creates"
    assert 'object_type_plural = "SECRETS"' in block
    assert '"USAGE"' in block


# --- stages, pools, repository --------------------------------------------------------------------


def test_every_stage_is_server_side_encrypted():
    blocks = resource_blocks(source("stages.tf"), "snowflake_stage_internal")
    assert blocks, "the root must create internal stages"
    for block in blocks:
        assert "snowflake_sse {}" in block, f"a stage without SNOWFLAKE_SSE encryption:\n{block}"


@pytest.mark.parametrize("stage", STAGES)
def test_the_naming_contracts_stages_exist(stage):
    assert f'"{stage}"' in source("locals.tf"), f"{stage} is missing from the stage list"


def test_the_image_repository_is_encrypted():
    block = hcl_block(source("image_repository.tf"), 'resource "snowflake_image_repository" "images"')
    assert 'encryption = "SNOWFLAKE_SSE"' in block


def test_compute_pools_suspend_themselves():
    block = hcl_block(source("compute_pools.tf"), 'resource "snowflake_compute_pool" "trust"')
    assert "auto_suspend_secs = var.pool_auto_suspend_secs" in block, "every pool must auto-suspend"
    assert 'initially_suspended = "true"' in block, "an apply must not start billing nodes"


def test_pool_auto_suspend_cannot_be_disabled():
    block = hcl_block(source("variables.tf"), 'variable "pool_auto_suspend_secs"')
    assert "var.pool_auto_suspend_secs > 0" in block, "a zero auto-suspend would mean a pool that never stops"


def test_the_gpu_pool_is_off_by_default():
    assert "default     = false" in hcl_block(source("variables.tf"), 'variable "enable_gpu_pool"')


def test_a_nightly_task_suspends_the_pools():
    block = hcl_block(source("cost.tf"), 'resource "snowflake_task" "suspend_pools"')
    assert block, "resource monitors do not cover SPCS: the nightly suspend task is mandatory"
    assert "ALTER COMPUTE POOL IF EXISTS" in block
    assert "started   = true" in block or "started = true" in block


def test_the_workload_root_creates_no_warehouse():
    """The warehouse is platform-owned with its resource monitor attached (assigning a monitor
    needs ACCOUNTADMIN); a warehouse this root could create could shed the monitor."""
    assert not resource_blocks(all_terraform(), "snowflake_warehouse")
    assert "resource_monitor" not in re.sub(r"#.*", "", all_terraform())


# --- account hygiene ------------------------------------------------------------------------------


def test_no_account_identifier_is_committed():
    for name in ("organization_name", "account_name"):
        block = hcl_block(source("variables.tf"), f'variable "{name}"')
        assert "default" not in block, f"{name} must be supplied at apply time, never defaulted"
    # Git, not the disk: an operator's own gitignored terraform.<env>.tfvars is exactly what the
    # README asks for, and must not fail the suite on their machine.
    tracked = subprocess.run(
        ["git", "ls-files", "--", "*.tfvars"], cwd=SNOWFLAKE_DIR, capture_output=True, text=True, check=True
    ).stdout.split()
    assert not tracked, f"a real tfvars file must never be tracked: {tracked}"


def test_gitignore_hides_state_tfvars_and_the_encryption_block():
    ignored = (SNOWFLAKE_DIR / ".gitignore").read_text()
    for pattern in ("*.tfstate", "*.tfvars", "encryption.tf", "backend_override.tf"):
        assert pattern in ignored, pattern


def test_the_backend_is_a_partial_configuration():
    block = hcl_block(source("backend.tf"), "terraform {")
    assert 'backend "local" {}' in block, "settings come from -backend-config, never from tracked values"


def test_no_auto_approve_anywhere():
    makefile = (SNOWFLAKE_DIR / "Makefile").read_text()
    assert "auto-approve" not in makefile, "apply and destroy must ask"


@pytest.mark.parametrize("target", ["init", "plan", "apply", "destroy", "lint", "test"])
def test_makefile_has_target(target):
    assert re.search(rf"^{target}:", (SNOWFLAKE_DIR / "Makefile").read_text(), re.M), target


@pytest.mark.parametrize(
    "output",
    ["image_repository_url", "compute_pool_names", "stage_names", "database_name", "role_names"],
)
def test_the_outputs_the_node_makefile_consumes(output):
    assert f'output "{output}"' in source("outputs.tf"), output


def test_no_dash_typography_in_prose():
    """House style: no em dash or en dash in prose, including comments and the README."""
    for path in [*TF_FILES, SNOWFLAKE_DIR / "README.md", SNOWFLAKE_DIR / "Makefile"]:
        text = path.read_text()
        assert "\u2014" not in text, f"{path.name} uses an em dash"
        assert "\u2013" not in text, f"{path.name} uses an en dash"
