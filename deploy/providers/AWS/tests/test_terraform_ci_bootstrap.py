# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Static guards on ``modules/terraform_ci_bootstrap`` and the ``ci/`` wrapper around it (FLIP#1199).

The module is a published interface: the platform repositories pin it by commit SHA and import the
live roles, boundary and state bucket into it. So besides the security properties of the policies,
these tests pin what an importer depends on — the resource addresses and the strings that would force
a replacement (``aws_iam_policy.description`` is ForceNew, and the boundary is attached to every FLIP
role).

Assertions compare whole normalised expressions, never substrings: an inverted condition, a swapped
reference or an extra list element must fail rather than pass on containment. The tiny HCL reader
below walks top-level arguments and nested blocks; it is not a full parser, just enough for these
files (every quoted string is skipped, so braces inside one do not count).

``TF_CI_BOOTSTRAP_MODULE_DIR`` points the module tests at another copy of the module, so a guard can
be mutation-tested without touching the real files.
"""

import os
import re
from pathlib import Path

from tf_source import strip_comments

AWS_PROVIDER_DIR = Path(__file__).resolve().parent.parent
MODULE_DIR = Path(os.environ.get("TF_CI_BOOTSTRAP_MODULE_DIR", AWS_PROVIDER_DIR / "modules" / "terraform_ci_bootstrap"))
CI_DIR = AWS_PROVIDER_DIR / "ci"

_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')
_IDENT = r"[A-Za-z_][\w-]*"

# Every IAM object the module declares, by address. The platform repositories import the live
# objects at exactly these addresses, and ci/'s moved blocks target them: a rename is a destroy and
# re-create of a role CI is running under.
IAM_ADDRESSES = {
    ("aws_iam_role", "terraform_plan"),
    ("aws_iam_role", "terraform_apply"),
    ("aws_iam_role_policy_attachment", "plan_read_only"),
    ("aws_iam_role_policy_attachment", "apply_power_user"),
    ("aws_iam_role_policy", "plan_deny_state_writes"),
    ("aws_iam_role_policy", "plan_read_flip_api_secret"),
    ("aws_iam_role_policy", "apply_iam"),
    ("aws_iam_role_policy", "apply_state"),
    ("aws_iam_policy", "apply_boundary"),
}

STATE_BUCKET_TYPES = {
    "aws_s3_bucket",
    "aws_s3_bucket_versioning",
    "aws_s3_bucket_public_access_block",
    "aws_s3_bucket_server_side_encryption_configuration",
    "aws_s3_bucket_lifecycle_configuration",
    "aws_s3_bucket_policy",
}

REQUIRED_MODULE_INPUTS = {
    "oidc_provider_arn",
    "github_org",
    "github_repo",
    "environment",
    "github_environment",
    "apply_branch",
    "state_bucket_name",
}


# --- a minimal HCL reader ------------------------------------------------------------------------


def _normalise(text: str) -> str:
    """Collapse layout outside string literals so formatting cannot change a comparison.

    Whitespace runs become one space, spaces next to brackets and commas go, and a trailing comma
    before a closing bracket goes. String literals are left byte for byte.

    Args:
        text (str): An HCL expression.

    Returns:
        str: The canonical form.
    """
    out, last = [], 0
    for match in _STRING.finditer(text):
        out.append(_normalise_code(text[last : match.start()]))
        out.append(match.group(0))
        last = match.end()
    out.append(_normalise_code(text[last:]))
    return "".join(out).strip()


def _normalise_code(code: str) -> str:
    code = re.sub(r"\s+", " ", code)
    code = re.sub(r"\s*([\[\](){},])\s*", r"\1", code)
    return re.sub(r",([\])}])", r"\1", code)


def _skip_string(text: str, index: int) -> int:
    match = _STRING.match(text, index)
    assert match is not None, f"unterminated string at {text[index : index + 40]!r}"
    return match.end()


def _balanced_end(text: str, index: int) -> int:
    """Index just past the bracket group opening at ``index``, skipping string literals."""
    depth = 0
    while index < len(text):
        char = text[index]
        if char == '"':
            index = _skip_string(text, index)
            continue
        if char in "[({":
            depth += 1
        elif char in "])}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    raise AssertionError("unbalanced brackets")


def _value_end(text: str, index: int) -> int:
    """Index where an argument's value ends: the first newline outside any bracket or string."""
    if text.startswith("<<", index):
        marker = re.match(r"<<-?(\w+)", text[index:]).group(1)
        return re.compile(rf"^\s*{marker}\s*$", re.M).search(text, index).end()
    while index < len(text) and text[index] != "\n":
        char = text[index]
        if char == '"':
            index = _skip_string(text, index)
        elif char in "[({":
            index = _balanced_end(text, index)
        else:
            index += 1
    return index


def _parse(body: str, raw: bool = False) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Split an HCL body into its top-level arguments and blocks.

    Args:
        body (str): File contents or a block body, whole-line comments already stripped.
        raw (bool): Return argument values as written instead of normalised (to parse an object value).

    Returns:
        tuple[dict[str, str], list[tuple[str, str]]]: ``{name: value}`` and
        ``[(normalised header, body)]``, in source order.
    """
    arguments: dict[str, str] = {}
    blocks: list[tuple[str, str]] = []
    index = 0
    while (start := re.compile(r"\S").search(body, index)) is not None:
        index = start.start()
        if argument := re.compile(rf"({_IDENT})\s*=(?!=)\s*").match(body, index):
            end = _value_end(body, argument.end())
            name = argument.group(1)
            assert name not in arguments, f"argument {name} set twice"
            value = re.sub(r"\s#.*$", "", body[argument.end() : end], flags=re.M)
            arguments[name] = value if raw else _normalise(value)
            index = end
            continue
        header = re.compile(rf"({_IDENT}(?:\s+(?:\"[^\"]*\"|{_IDENT}))*)\s*\{{").match(body, index)
        assert header is not None, f"cannot read HCL at {body[index : index + 60]!r}"
        open_brace = header.end() - 1
        close = _balanced_end(body, open_brace)
        blocks.append((re.sub(r"\s+", " ", header.group(1)), body[open_brace + 1 : close - 1]))
        index = close
    return arguments, blocks


def _arguments(body: str) -> dict[str, str]:
    return _parse(body)[0]


def _blocks(body: str, header: str) -> list[str]:
    """Bodies of every top-level block whose header is exactly ``header``."""
    return [block for found, block in _parse(body)[1] if found == header]


def _block(body: str, header: str) -> str:
    """The body of the one top-level block whose header is exactly ``header``."""
    found = _blocks(body, header)
    assert len(found) == 1, f"expected exactly one {header!r} block, found {len(found)}"
    return found[0]


def _list(expression: str) -> list[str]:
    """Elements of a normalised list literal of simple expressions."""
    assert expression.startswith("["), f"not a list literal: {expression}"
    assert expression.endswith("]"), f"not a list literal: {expression}"
    inner = expression[1:-1]
    return inner.split(",") if inner else []


# --- the module ----------------------------------------------------------------------------------


def _module_files() -> dict[str, str]:
    return {path.name: strip_comments(path.read_text()) for path in sorted(MODULE_DIR.glob("*.tf"))}


def _module_blocks() -> list[tuple[str, str]]:
    return [block for source in _module_files().values() for block in _parse(source)[1]]


def _module_block(header: str) -> str:
    found = [body for found, body in _module_blocks() if found == header]
    assert len(found) == 1, f"expected exactly one {header!r} in the module, found {len(found)}"
    return found[0]


def _resource_addresses() -> set[tuple[str, str]]:
    addresses = set()
    for header, _ in _module_blocks():
        match = re.fullmatch(r'resource "([^"]+)" "([^"]+)"', header)
        if match:
            addresses.add((match.group(1), match.group(2)))
    return addresses


def _module_locals() -> dict[str, str]:
    merged: dict[str, str] = {}
    for header, body in _module_blocks():
        if header == "locals":
            merged.update(_arguments(body))
    return merged


def _statement(document: str, sid: str) -> str:
    """The static ``statement`` block with this sid in a policy document."""
    found = [
        body for body in _blocks(_module_block(document), "statement") if _arguments(body).get("sid") == f'"{sid}"'
    ]
    assert len(found) == 1, f"expected exactly one statement {sid} in {document}, found {len(found)}"
    return found[0]


def _conditions(statement: str) -> list[dict[str, str]]:
    return [_arguments(body) for body in _blocks(statement, "condition")]


def test_the_iam_addresses_are_the_ones_imports_and_moves_target() -> None:
    """Exactly the nine IAM objects, at the names ci/ used — no rename, no extra, no missing one."""
    iam = {address for address in _resource_addresses() if address[0].startswith("aws_iam_")}
    assert iam == IAM_ADDRESSES


def test_the_state_bucket_resources_are_all_named_state() -> None:
    """The platform repositories import the existing buckets at ``…["state"][0]``."""
    s3 = {address for address in _resource_addresses() if address[0].startswith("aws_s3_")}
    assert s3 == {(resource_type, "state") for resource_type in STATE_BUCKET_TYPES}


def test_the_boundary_description_never_changes() -> None:
    """``aws_iam_policy.description`` is ForceNew, and the policy is the boundary on every FLIP role."""
    boundary = _arguments(_module_block('resource "aws_iam_policy" "apply_boundary"'))
    assert boundary["description"] == '"Permissions boundary for roles the FLIP Terraform pipeline creates (FLIP#962)"'
    assert boundary["name"] == "var.permissions_boundary_name"


def test_the_role_strings_match_the_live_roles() -> None:
    """Descriptions, session length and inline-policy names are what the import plans proved equal."""
    plan = _arguments(_module_block('resource "aws_iam_role" "terraform_plan"'))
    assert plan["description"] == '"Read-only role for FLIP Terraform plans from GitHub Actions (FLIP#962)"'
    assert plan["max_session_duration"] == "3600"
    apply = _arguments(_module_block('resource "aws_iam_role" "terraform_apply"'))
    assert (
        apply["description"]
        == '"Role for FLIP Terraform applies from GitHub Actions on ${var.apply_branch} (FLIP#962)"'
    )
    assert apply["max_session_duration"] == "3600"
    inline = {
        name: _arguments(_module_block(f'resource "aws_iam_role_policy" "{name}"'))["name"]
        for name in ("plan_deny_state_writes", "plan_read_flip_api_secret", "apply_iam", "apply_state")
    }
    assert inline == {
        "plan_deny_state_writes": '"deny-terraform-state-writes"',
        "plan_read_flip_api_secret": '"flip-terraform-plan-read-secret"',
        "apply_iam": '"flip-terraform-apply-iam"',
        "apply_state": '"flip-terraform-apply-state"',
    }


def test_only_the_account_specific_inputs_are_required() -> None:
    """``github_org`` / ``github_repo`` especially: a default would make an adopter trust AI Centre's workflows."""
    variables = {header.split('"')[1]: body for header, body in _module_blocks() if header.startswith("variable ")}
    required = {name for name, body in variables.items() if "default" not in _arguments(body)}
    assert required == REQUIRED_MODULE_INPUTS


def test_the_module_owns_no_provider_backend_or_oidc_provider() -> None:
    """The caller supplies the provider and the state; the OIDC provider is account plumbing, passed in."""
    headers = [header for header, _ in _module_blocks()]
    assert not [header for header in headers if header.split()[0] == "provider"]
    assert not [header for header in headers if header.startswith('resource "aws_iam_openid_connect_provider"')]
    for body in (body for header, body in _module_blocks() if header == "terraform"):
        assert not [header for header, _ in _parse(body)[1] if header.startswith("backend")]
    assert 'variable "oidc_provider_arn"' in headers
    for document in ("plan_assume_role", "apply_assume_role"):
        statement = _block(_module_block(f'data "aws_iam_policy_document" "{document}"'), "statement")
        principals = _arguments(_block(statement, "principals"))
        assert principals == {"type": '"Federated"', "identifiers": "[var.oidc_provider_arn]"}


def test_the_aws_provider_constraint_spans_both_platform_locks() -> None:
    """aicentre-iac locks 6.39 and FLIP 6.66; a major version is a deliberate bump."""
    terraform = _module_block("terraform")
    providers = _parse(_block(terraform, "required_providers"), raw=True)[0]
    aws = _arguments(providers["aws"].strip()[1:-1])
    assert aws == {"source": '"hashicorp/aws"', "version": '">= 6.0, < 7.0"'}
    assert _arguments(terraform)["required_version"] == '">= 1.13.1"'


def test_the_trust_policies_pin_the_environment_and_the_apply_ref() -> None:
    """``sub`` carries the environment; ``job_workflow_ref`` pins the branch, exactly, for apply."""
    locals_ = _module_locals()
    assert locals_["oidc_sub"] == '"repo:${local.repo}:environment:${var.github_environment}"'
    assert locals_["apply_workflow_ref"] == (
        '"${local.workflow_ref_prefix}/${var.apply_workflow_file}@refs/heads/${var.apply_branch}"'
    )
    apply = _block(_module_block('data "aws_iam_policy_document" "apply_assume_role"'), "statement")
    assert _conditions(apply) == [
        {
            "test": '"StringEquals"',
            "variable": '"token.actions.githubusercontent.com:aud"',
            "values": '["sts.amazonaws.com"]',
        },
        {
            "test": '"StringEquals"',
            "variable": '"token.actions.githubusercontent.com:sub"',
            "values": "[local.oidc_sub]",
        },
        {
            "test": '"StringEquals"',
            "variable": '"token.actions.githubusercontent.com:job_workflow_ref"',
            "values": "[local.apply_workflow_ref]",
        },
    ]


def test_the_state_bucket_cannot_be_destroyed() -> None:
    lifecycle = _arguments(_block(_module_block('resource "aws_s3_bucket" "state"'), "lifecycle"))
    assert lifecycle == {"prevent_destroy": "true"}


def test_the_state_bucket_blocks_all_public_access() -> None:
    block = _arguments(_module_block('resource "aws_s3_bucket_public_access_block" "state"'))
    flags = {name: value for name, value in block.items() if name not in {"count", "bucket"}}
    assert flags == {
        "block_public_acls": "true",
        "block_public_policy": "true",
        "ignore_public_acls": "true",
        "restrict_public_buckets": "true",
    }


def test_the_state_bucket_refuses_plaintext_transport() -> None:
    """State holds credentials in clear; every request without TLS is denied, to everyone."""
    statement = _statement('data "aws_iam_policy_document" "state_bucket"', "DenyInsecureTransport")
    arguments = _arguments(statement)
    assert arguments["effect"] == '"Deny"'
    assert arguments["actions"] == '["s3:*"]'
    assert arguments["resources"] == "[local.state_arn,local.state_all_objects_arn]"
    assert _arguments(_block(statement, "principals")) == {"type": '"*"', "identifiers": '["*"]'}
    assert _conditions(statement) == [{"test": '"Bool"', "variable": '"aws:SecureTransport"', "values": '["false"]'}]


def test_the_state_write_restriction_renders_only_when_switched_on() -> None:
    """Off by default; when on, every principal but the writers is denied — the apply role always a writer."""
    document = _module_block('data "aws_iam_policy_document" "state_bucket"')
    static_sids = [_arguments(body).get("sid") for body in _blocks(document, "statement")]
    assert '"DenyStateWritesExceptTheWriters"' not in static_sids, "the restriction must not be unconditional"

    dynamic = _block(document, 'dynamic "statement"')
    assert _arguments(dynamic)["for_each"] == _normalise("var.restrict_state_writes ? [1] : []")
    content = _block(dynamic, "content")
    arguments = _arguments(content)
    assert arguments["sid"] == '"DenyStateWritesExceptTheWriters"'
    assert arguments["effect"] == '"Deny"'
    assert arguments["resources"] == "[local.state_all_objects_arn]"
    assert _conditions(content) == [
        {"test": '"ArnNotLike"', "variable": '"aws:PrincipalArn"', "values": "local.state_writer_arn_patterns"}
    ]
    assert _module_locals()["state_writer_arn_patterns"] == (
        "concat([aws_iam_role.terraform_apply.arn],var.state_writer_principal_arns)"
    )

    restrict = _module_block('variable "restrict_state_writes"')
    assert _arguments(restrict)["default"] == "false"


def test_the_apply_role_cannot_escalate_itself() -> None:
    statement = _statement('data "aws_iam_policy_document" "apply_iam"', "NoSelfEscalation")
    arguments = _arguments(statement)
    assert arguments["effect"] == '"Deny"'
    assert sorted(_list(arguments["resources"])) == [
        "aws_iam_role.terraform_apply.arn",
        "aws_iam_role.terraform_plan.arn",
    ]
    assert set(_list(arguments["actions"])) == {
        '"iam:AttachRolePolicy"',
        '"iam:DeleteRole"',
        '"iam:DeleteRolePermissionsBoundary"',
        '"iam:DeleteRolePolicy"',
        '"iam:DetachRolePolicy"',
        '"iam:PassRole"',
        '"iam:PutRolePermissionsBoundary"',
        '"iam:PutRolePolicy"',
        '"iam:UpdateAssumeRolePolicy"',
        '"iam:UpdateRole"',
    }


def test_the_apply_role_cannot_rewrite_its_boundary() -> None:
    statement = _statement('data "aws_iam_policy_document" "apply_iam"', "NoBoundaryTampering")
    arguments = _arguments(statement)
    assert arguments["effect"] == '"Deny"'
    assert arguments["resources"] == "[aws_iam_policy.apply_boundary.arn]"
    assert set(_list(arguments["actions"])) == {
        '"iam:CreatePolicyVersion"',
        '"iam:DeletePolicy"',
        '"iam:DeletePolicyVersion"',
        '"iam:SetDefaultPolicyVersion"',
    }


def test_new_roles_must_carry_the_boundary() -> None:
    """CreateRole / PutRolePolicy / AttachRolePolicy / PutRolePermissionsBoundary all name this boundary."""
    for sid in (
        "CreateAndGrantOnlyInsideTheBoundary",
        "AttachOnlyTheManagedPoliciesThisRootUses",
        "SetTheBoundaryItself",
    ):
        conditions = _conditions(_statement('data "aws_iam_policy_document" "apply_iam"', sid))
        assert {
            "test": '"StringEquals"',
            "variable": '"iam:PermissionsBoundary"',
            "values": "[aws_iam_policy.apply_boundary.arn]",
        } in conditions, sid


def test_the_plan_role_cannot_write_state() -> None:
    document = _module_block('data "aws_iam_policy_document" "plan_deny_state_writes"')
    arguments = _arguments(_block(document, "statement"))
    assert arguments["effect"] == '"Deny"'
    assert arguments["resources"] == "[local.state_arn,local.state_all_objects_arn]"
    assert _module_locals()["state_all_objects_arn"] == '"${local.state_arn}/*"'


# --- the ci/ wrapper -----------------------------------------------------------------------------


def _ci(name: str) -> str:
    return strip_comments((CI_DIR / name).read_text())


LEGACY_ADDRESSES = sorted(f"{resource_type}.{name}" for resource_type, name in IAM_ADDRESSES)


def test_the_wrapper_has_no_ai_centre_mode_table() -> None:
    """Generic: no PROD token, no operator env file — the tfvars file and the account guard are the inputs."""
    makefile = "\n".join(line for line in (CI_DIR / "Makefile").read_text().splitlines() if not line.startswith("#"))
    assert not re.search(r"\bPROD\b", makefile)
    assert not re.search(r"^\s*-?include\b", makefile, re.M)
    assert ".env." not in makefile


def test_the_wrapper_commits_no_backend() -> None:
    """State is local until ``make migrate-state`` writes backend.tf (gitignored) from the example."""
    for path in CI_DIR.glob("*.tf"):
        if path.name == "backend.tf":
            continue  # the operator's own, written by migrate-state and gitignored
        for header, body in _parse(strip_comments(path.read_text()))[1]:
            if header == "terraform":
                assert not [block for block, _ in _parse(body)[1] if block.startswith("backend")], path.name
    assert "backend.tf" in (CI_DIR / ".gitignore").read_text().split()
    example = _block(_parse(strip_comments((CI_DIR / "backend.s3.tf.example").read_text()))[1][0][1], 'backend "s3"')
    assert _arguments(example) == {"key": '"flip/ci/terraform.tfstate"', "encrypt": "true", "use_lockfile": "true"}


def test_the_wrapper_moves_every_legacy_address_into_the_module() -> None:
    """Someone who applied the old root, which declared these directly, stays at zero diff."""
    moves = sorted(tuple(_arguments(body).values()) for body in _blocks(_ci("main.tf"), "moved"))
    assert moves == [(address, f"module.terraform_ci.{address}") for address in LEGACY_ADDRESSES]


def test_the_wrapper_guards_the_account() -> None:
    main = _ci("main.tf")
    provider = _arguments(_block(main, 'provider "aws"'))
    assert provider == {"region": "var.aws_region", "allowed_account_ids": "var.allowed_account_ids"}
    validation = _arguments(_block(_block(_ci("variables.tf"), 'variable "allowed_account_ids"'), "validation"))
    assert validation["condition"] == _normalise("length(var.allowed_account_ids) > 0")


def test_the_wrapper_passes_the_looked_up_oidc_provider_and_every_input() -> None:
    main = _ci("main.tf")
    module = _arguments(_block(main, 'module "terraform_ci"'))
    assert module.pop("source") == '"../modules/terraform_ci_bootstrap"'
    assert module.pop("oidc_provider_arn") == "data.aws_iam_openid_connect_provider.github.arn"
    module_inputs = {header.split('"')[1] for header, _ in _module_blocks() if header.startswith("variable ")}
    assert module == {name: f"var.{name}" for name in module_inputs - {"oidc_provider_arn"}}

    wrapper_variables = {header.split('"')[1]: _arguments(body) for header, body in _parse(_ci("variables.tf"))[1]}
    required = {name for name, arguments in wrapper_variables.items() if "default" not in arguments}
    assert required == (REQUIRED_MODULE_INPUTS - {"oidc_provider_arn"}) | {"aws_region", "allowed_account_ids"}
    module_defaults = {
        header.split('"')[1]: _arguments(body).get("default")
        for header, body in _module_blocks()
        if header.startswith("variable ")
    }
    for name, arguments in wrapper_variables.items():
        if "default" in arguments:
            assert arguments["default"] == module_defaults[name], f"{name} default differs from the module's"
