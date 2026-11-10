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

"""Static guards for the Snowflake identity root (FLIP#1581, epic #1589). Credential-free.

This root creates the six FLIP roles, their hierarchy and the deploy SERVICE user, and nothing
else: no object that holds data, no privilege on one, no person user. It exists as its own root so
it can be applied by the role that administers users (USERADMIN) on an account where nobody hands
out ACCOUNTADMIN, with one role and therefore one session.
"""

from __future__ import annotations

import re
import subprocess

import pytest
from conftest import PLATFORM_DIR, ROOT_DIR, TF_FILES, all_terraform, source
from tf_blocks import hcl_block, resource_blocks

ROOT_FILES = (
    "versions.tf",
    "backend.tf",
    "providers.tf",
    "variables.tf",
    "locals.tf",
    "roles.tf",
    "user.tf",
    "outputs.tf",
)
ROLES = ("DEPLOY", "SVC", "OMOP_READER", "OMOP_LOADER", "OBSERVER", "CONVERTER")
IDENTITY_RESOURCES = {"snowflake_account_role", "snowflake_grant_account_role", "snowflake_service_user"}


@pytest.mark.parametrize("name", ROOT_FILES)
def test_the_root_has_its_files(name):
    assert (ROOT_DIR / name).is_file(), name


@pytest.mark.parametrize("path", TF_FILES, ids=lambda p: p.name)
def test_apache_header(path):
    assert "Licensed under the Apache License, Version 2.0" in path.read_text(), path.name


# --- roles ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ROLES)
def test_the_six_roles_exist_under_the_shared_names(role):
    name = f'"${{local.prefix}}_{role}"'
    assert name in source("locals.tf"), role
    assert name in (PLATFORM_DIR / "locals.tf").read_text(), f"{role}: the platform root must derive the same name"
    assert hcl_block(source("roles.tf"), f'resource "snowflake_account_role" "{role.lower()}"'), role


def test_the_admin_role_is_retired():
    assert '_ADMIN"' not in all_terraform(), "FLIP_<CODE>_ADMIN is retired (epic #1589)"


def test_the_consumer_roles_are_granted_to_the_deploy_role():
    """So the workload root can grant on its own objects without MANAGE GRANTS."""
    block = hcl_block(source("roles.tf"), 'resource "snowflake_grant_account_role" "to_deploy"')
    assert block, "the consumer roles must be handed to the deploy role"
    for role in ("svc", "omop_reader", "omop_loader", "observer", "converter"):
        assert re.search(rf"{role}\s+= snowflake_account_role.{role}.name", block), role
    assert "parent_role_name = snowflake_account_role.deploy.name" in block


def test_a_service_inherits_omop_read_and_nothing_else():
    """SPCS services run as FLIP_<CODE>_SVC. OBSERVER (the logs) and CONVERTER (CREATE SERVICE) are
    reachable through the deploy role only, never from inside a container."""
    grants = resource_blocks(all_terraform(), "snowflake_grant_account_role")
    into_svc = [b for b in grants if "parent_role_name = snowflake_account_role.svc.name" in b]
    assert len(into_svc) == 1, "SVC must inherit exactly one role"
    assert "role_name        = snowflake_account_role.omop_reader.name" in into_svc[0]
    for block in grants:
        for role in ("observer", "converter"):
            if f"snowflake_account_role.{role}.name" in block and "for_each" not in block:
                raise AssertionError(f"{role} is reachable only through the deploy role:\n{block}")


# --- the deploy user ------------------------------------------------------------------------------


def test_the_deploy_identity_is_a_service_user_with_a_public_key():
    block = hcl_block(source("user.tf"), 'resource "snowflake_service_user" "deploy"')
    assert block, "the deploy identity must be a SERVICE user"
    assert "rsa_public_key = var.deploy_user_public_key" in block
    assert not re.search(r"^\s*password\s*=", block, re.M)


def test_the_deploy_user_holds_the_deploy_role_only():
    to_users = [b for b in resource_blocks(all_terraform(), "snowflake_grant_account_role") if "user_name" in b]
    assert len(to_users) == 1
    assert "role_name = snowflake_account_role.deploy.name" in to_users[0]


def test_no_person_user_is_managed_here():
    assert not resource_blocks(all_terraform(), "snowflake_user"), "person users are the site's own, never Terraform's"


# --- scope ----------------------------------------------------------------------------------------


def test_this_root_creates_identities_and_nothing_else():
    created = set(re.findall(r'^resource "([a-z_]+)" ', all_terraform(), re.M))
    assert created == IDENTITY_RESOURCES, created


def test_no_privilege_is_granted_here():
    assert "snowflake_grant_privileges" not in all_terraform(), (
        "privileges on objects belong to the roots that own them"
    )


def test_the_applying_role_is_supplied_at_apply_time():
    block = hcl_block(source("variables.tf"), 'variable "identity_role"')
    assert not re.search(r"^\s*default\s*=", block, re.M), "identity_role must not be defaulted"
    for path in TF_FILES:
        assert not re.search(r'=\s*"(ACCOUNTADMIN|SECURITYADMIN|USERADMIN)"', path.read_text()), path.name
    assert "role              = var.identity_role" in hcl_block(source("providers.tf"), 'provider "snowflake"')


def test_one_role_one_session():
    """A one-time passcode is accepted once, so a root that opens two sessions cannot be applied
    with one. This root has a single provider configuration on purpose."""
    assert all_terraform().count('provider "snowflake" {') == 1
    assert "alias" not in source("providers.tf")


@pytest.mark.parametrize("field", ["password", "passcode", "private_key", "token"])
def test_no_credential_is_a_terraform_variable(field):
    for path in TF_FILES:
        assert not re.search(rf'variable "[a-z_]*{field}[a-z_]*"', path.read_text()), (
            f"{path.name}: {field} must come from the environment, never from a variable"
        )


# --- hygiene --------------------------------------------------------------------------------------


def test_no_account_identifier_is_committed():
    for name in ("organization_name", "account_name"):
        assert "default" not in hcl_block(source("variables.tf"), f'variable "{name}"'), name
    tracked = subprocess.run(
        ["git", "ls-files", "--", "*.tfvars"], cwd=ROOT_DIR, capture_output=True, text=True, check=True
    ).stdout.split()
    assert not tracked, f"a real tfvars file must never be tracked: {tracked}"


def test_gitignore_hides_state_tfvars_and_the_encryption_block():
    ignored = (ROOT_DIR / ".gitignore").read_text()
    for pattern in ("*.tfstate", "*.tfvars", "encryption.tf", "backend_override.tf"):
        assert pattern in ignored, pattern


def test_the_three_roots_keep_separate_state():
    assert 'backend "local" {}' in hcl_block(source("backend.tf"), "terraform {")
    assert "terraform-identity.$(ENV).tfstate" in (ROOT_DIR / "Makefile").read_text()


@pytest.mark.parametrize("target", ["init", "plan", "apply", "destroy", "output", "lint", "test"])
def test_makefile_has_target(target):
    assert re.search(rf"^{target}:", (ROOT_DIR / "Makefile").read_text(), re.M), target
