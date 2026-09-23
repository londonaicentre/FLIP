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

"""Static guards on releasing the public web alias (FLIP#749 change 2b).

``var.release_web_alias`` hands ``flip_alb_subdomain`` to another estate's CloudFront
distribution. Dropping the alias here — not moving DNS — is what actually moves the name,
because CloudFront resolves a request by ``Host`` header against alternate domain names
that are unique across every AWS account, preferring an exact alias over a wildcard one.
The runbook is in README.md, "Handing the public name over".

Three invariants, none of them reachable from a runtime test in this repo:

* ``aliases`` and **every** field of ``viewer_certificate`` key to the same condition.
  CloudFront permits a custom viewer certificate only while the distribution carries an
  alias, so a divergence is a configuration AWS rejects — at the cutover apply.
* The flag defaults false and is independent of ``var.manage_dns``. Otherwise the alias
  drops on whatever apply next reaches production, which is an outage at an unplanned
  moment rather than a chosen window.
* Both certificate resources survive the release, and ``local.ui_origin`` does not follow
  the gate. Sweeping either onto it "for consistency" turns a rollback into a
  DNS-validation wait, or silently repoints Cognito and the bucket CORS mid-cutover.

**These assertions compare normalised expressions, not substrings.** An earlier revision
matched on containment and was defeated by every mutation that mattered: ``&&`` for
``||`` left the gate always-true, an inverted ternary left the alias permanently attached,
and a field made *unconditional* was invisible to a scan for ``?``. Containment also broke
on reformatting a ternary across lines. Compare whole expressions, and pin the set of
fields rather than iterating whatever happens to be there.
"""

import re
from pathlib import Path

import pytest
from tf_source import hcl_block, strip_comments

AWS_PROVIDER_DIR = Path(__file__).resolve().parent.parent
CLOUDFRONT_TF = AWS_PROVIDER_DIR / "cloudfront.tf"
VARIABLES_TF = AWS_PROVIDER_DIR / "variables.tf"
MAKEFILE = AWS_PROVIDER_DIR / "Makefile"
COMPOSE_CI_ENV = AWS_PROVIDER_DIR / "scripts" / "compose-ci-env.sh"

GATE = "local.serves_public_name"

# Pinned as a set: the failure this file exists to prevent is a field leaving the
# conditional, and iterating only what is present cannot see an absence.
VIEWER_CERTIFICATE_FIELDS = {
    "acm_certificate_arn",
    "cloudfront_default_certificate",
    "ssl_support_method",
    "minimum_protocol_version",
}

ASSIGNMENT = re.compile(r"^(\s*)([a-z_][a-z0-9_]*)\s*=\s*(.*)$")


def _normalise(text: str) -> str:
    """Drop trailing comments and collapse whitespace so layout cannot change meaning.

    ``tf_source.strip_comments`` only removes whole-line comments, which leaves a trailing
    ``# …`` able to inject stray characters into an expression.

    Args:
        text (str): Terraform source text.

    Returns:
        str: The text with comments removed and runs of whitespace collapsed to one space.
    """
    collapsed = re.sub(r"\s+", " ", re.sub(r"#.*$|//.*$", "", text, flags=re.M)).strip()
    return _strip_wrapping_parentheses(collapsed)


def _strip_wrapping_parentheses(expression: str) -> str:
    """Remove a parenthesis pair that encloses the whole expression.

    ``terraform fmt`` accepts ``(a && b)`` and a parenthesised multi-line ternary as
    reformattings of the bare forms, so an assertion comparing whole expressions has to
    see through them or it fails on a no-op edit.

    Args:
        expression (str): A normalised expression.

    Returns:
        str: The expression without redundant enclosing parentheses.
    """
    while expression.startswith("(") and expression.endswith(")"):
        depth = 0
        for index, character in enumerate(expression):
            depth += (character == "(") - (character == ")")
            if depth == 0 and index < len(expression) - 1:
                return expression  # the opening paren closes early, so it wraps only part
        expression = expression[1:-1].strip()
    return expression


def _right_hand_side(block: str, name: str) -> str:
    """Return the normalised right-hand side of a top-level assignment in ``block``.

    Continuation lines are joined, so an expression wrapped by ``terraform fmt`` reads the
    same as a single-line one.

    Args:
        block (str): An HCL block body.
        name (str): The argument name, e.g. ``aliases``.

    Returns:
        str: The assigned expression, normalised.
    """
    collected: list[str] | None = None
    indent = ""
    for line in block.splitlines():
        match = ASSIGNMENT.match(line)
        if collected is not None:
            if (match and len(match.group(1)) <= len(indent)) or line.strip() in ("}", "]"):
                break
            collected.append(line)
            continue
        if match and match.group(2) == name:
            indent, collected = match.group(1), [match.group(3)]
    assert collected is not None, f"{name} is not assigned in this block"
    return _normalise("\n".join(collected))


def _arguments(block: str) -> dict[str, str]:
    """Return every ``name = value`` in ``block``, independent of layout.

    Args:
        block (str): An HCL block body with no nested blocks.

    Returns:
        dict[str, str]: Argument name to normalised expression.
    """
    flat = _normalise(block)
    found: dict[str, str] = {}
    for match in re.finditer(r"([a-z_][a-z0-9_]*)\s*=\s*", flat):
        rest = flat[match.end() :]
        following = re.search(r"\s[a-z_][a-z0-9_]*\s*=\s*", rest)
        found[match.group(1)] = _normalise(rest[: following.start()] if following else rest)
    return found


def _ui_distribution() -> str:
    """The flip_ui distribution block, whole-line comments stripped.

    Returns:
        str: The brace-balanced block body.
    """
    return hcl_block(strip_comments(CLOUDFRONT_TF.read_text()), 'resource "aws_cloudfront_distribution" "flip_ui"')


def test_serves_public_name_is_the_conjunction() -> None:
    """Both conditions, and ``&&``: with ``||`` the release flag is a no-op."""
    expression = _right_hand_side(strip_comments(CLOUDFRONT_TF.read_text()), "serves_public_name")
    assert expression == "var.manage_dns && !var.release_web_alias", (
        "the gate must be the conjunction — an `||` here reads as correct, passes a "
        f"containment check, and makes the cutover silently do nothing: {expression}"
    )


def test_aliases_are_keyed_to_the_gate() -> None:
    """The public name is carried only while the gate holds, and nothing else is."""
    expression = _right_hand_side(_ui_distribution(), "aliases")
    assert expression == f"{GATE} ? [var.flip_alb_subdomain] : []", (
        "aliases must carry flip_alb_subdomain only while the gate holds; an inverted "
        f"ternary or a hardcoded false branch means the name never moves: {expression}"
    )


def test_every_viewer_certificate_field_uses_the_same_gate() -> None:
    """A custom certificate is legal only alongside an alias, so the two cannot diverge."""
    fields = _arguments(hcl_block(_ui_distribution(), "viewer_certificate"))
    assert set(fields) == VIEWER_CERTIFICATE_FIELDS, (
        f"viewer_certificate's fields changed: {set(fields) ^ VIEWER_CERTIFICATE_FIELDS}. "
        "Pinning the set is the point — a field made unconditional is how this invariant "
        "actually breaks, and an absence is invisible to any scan of what is present."
    )
    for name, expression in sorted(fields.items()):
        assert re.match(rf"^\(?\s*{re.escape(GATE)}\s*\?", expression), (
            f"{name} must be conditional on {GATE}. CloudFront rejects a custom certificate "
            f"on a distribution with no alias, and the rejection lands during the cutover "
            f"apply: {expression}"
        )


@pytest.mark.parametrize(
    "resource",
    [
        'resource "aws_acm_certificate" "flip_cloudfront"',
        'resource "aws_acm_certificate_validation" "flip_cloudfront"',
    ],
)
def test_certificate_resources_stay_gated_on_manage_dns(resource: str) -> None:
    """Both of them: the distribution indexes ``[0]`` off the *validation* resource."""
    count = _arguments(hcl_block(strip_comments(CLOUDFRONT_TF.read_text()), resource))["count"]
    assert count == "var.manage_dns ? 1 : 0", (
        f"{resource} must survive an alias release. Gating it on the release flag makes the "
        f"[0] reference in viewer_certificate an invalid index at the cutover apply, and "
        f"turns rollback into a DNS-validation wait rather than a re-apply: {count}"
    )


def test_ui_origin_does_not_follow_the_release_gate() -> None:
    """The browser origin is still the public name after the handover — another estate serves it.

    It feeds the Cognito ``callback_urls`` and ``logout_urls``, the S3 CORS rules and the
    invite-email hostname (services.tf). Sweeping it onto the gate alongside its new
    neighbour flips it to the ``*.cloudfront.net`` domain mid-cutover, so sign-in and
    uploads break with nothing red in Terraform.
    """
    expression = _right_hand_side(strip_comments(CLOUDFRONT_TF.read_text()), "ui_origin")
    assert GATE not in expression, (
        f"local.ui_origin must stay keyed to var.manage_dns, not to the release gate: {expression}"
    )
    assert "release_web_alias" not in expression, (
        f"local.ui_origin must not reference the release flag directly either: {expression}"
    )


def test_variable_is_a_bool_defaulting_false() -> None:
    """Absent means "this account keeps the name" — the safe value for every estate."""
    block = _arguments(hcl_block(strip_comments(VARIABLES_TF.read_text()), 'variable "release_web_alias"'))
    assert block.get("type") == "bool", (
        f"release_web_alias must be typed bool; `!var.release_web_alias` on a string is a "
        f"plan-time error: {block.get('type')}"
    )
    assert block.get("default") == "false", (
        "release_web_alias must default to false; any other default hands the public name "
        f"away on the next apply that reaches production: {block.get('default')}"
    )


def test_makefile_exports_the_variable_defaulting_false() -> None:
    """The make-level default has to agree with the Terraform one."""
    source = MAKEFILE.read_text()
    assert re.search(r"^RELEASE_WEB_ALIAS\s*\?=\s*false\s*$", source, re.M), (
        "Makefile must default RELEASE_WEB_ALIAS to false"
    )
    assert "export TF_VAR_release_web_alias=$(RELEASE_WEB_ALIAS)" in source, (
        "Makefile must export the variable to Terraform"
    )


def test_release_web_alias_is_optional_not_required() -> None:
    """Optional is the correct half of the manifest, and the choice is load-bearing.

    ``scripts/tests/test_compose_ci_env.sh`` already proves the key reaches the manifest and
    all three workflows — it derives the list from the Makefile's own ``export TF_VAR_`` lines
    — so duplicating that here would be weaker, not extra. What it does not pin is *which*
    array the key belongs to. Required is reserved for keys whose Makefile default is
    destructive; this one's default is the safe direction, so making it required would fail
    every ordinary plan until both GitHub environments set it.
    """
    optional = re.search(r"OPTIONAL_KEYS=\((.*?)\n\)", COMPOSE_CI_ENV.read_text(), re.S)
    assert optional is not None, "OPTIONAL_KEYS array not found in compose-ci-env.sh"
    assert re.search(r"^\s*RELEASE_WEB_ALIAS\s*$", strip_comments(optional.group(1)), re.M), (
        "RELEASE_WEB_ALIAS must be a member of OPTIONAL_KEYS — absent means false, which is "
        "this account keeping the name"
    )
