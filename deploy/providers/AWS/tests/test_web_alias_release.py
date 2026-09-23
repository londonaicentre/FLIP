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
distribution. It exists because CloudFront resolves a request by ``Host`` header against
alternate domain names that are unique across *every* AWS account, and prefers an exact
alias over a wildcard one — so while this account's distribution still lists the name it
keeps serving it no matter where DNS points. Moving DNS is therefore not the switch;
dropping the alias here is.

Two invariants, and both fail in ways no runtime test in this repo can reach — the first
at apply time against real AWS, the second silently at the cutover.

* **The alias and the viewer certificate move together.** CloudFront permits a custom
  viewer certificate only while the distribution carries an alias. Keying ``aliases`` to
  the release flag while the certificate stays on ``var.manage_dns`` produces a
  configuration AWS rejects, and it is rejected during the cutover apply — the worst
  possible moment. So every field of ``viewer_certificate`` must key to the same
  condition as ``aliases``.

* **The flag defaults to false and is a switch of its own.** Defaulting it true, or
  folding it into ``var.manage_dns``, would drop the alias on the next apply that reaches
  production — whenever a release merges, rather than in a chosen window. ``manage_dns``
  must stay independent: this account keeps its zone, records and certificate when the
  name is released, which is what makes putting the alias back a single re-apply.

The certificate resource itself stays gated on ``manage_dns`` rather than on the release
flag, deliberately: destroying and re-issuing it would turn a rollback into a DNS
validation wait. That is asserted too, since "tidying" it to match the others reads like
an obvious simplification.
"""

import re
from pathlib import Path

from tf_source import hcl_block, strip_comments

AWS_PROVIDER_DIR = Path(__file__).resolve().parent.parent
CLOUDFRONT_TF = AWS_PROVIDER_DIR / "cloudfront.tf"
VARIABLES_TF = AWS_PROVIDER_DIR / "variables.tf"
MAKEFILE = AWS_PROVIDER_DIR / "Makefile"
COMPOSE_CI_ENV = AWS_PROVIDER_DIR / "scripts" / "compose-ci-env.sh"
WORKFLOWS = tuple(
    AWS_PROVIDER_DIR.parents[2] / ".github" / "workflows" / name
    for name in ("terraform_plan.yml", "terraform_apply.yml", "terraform_drift.yml")
)

GATE = "local.serves_public_name"


def _ui_distribution() -> str:
    """The flip_ui distribution block, comments stripped.

    Returns:
        str: The brace-balanced block body with comment lines removed.
    """
    source = strip_comments(CLOUDFRONT_TF.read_text())
    return hcl_block(source, 'resource "aws_cloudfront_distribution" "flip_ui"')


def test_serves_public_name_is_manage_dns_and_not_released() -> None:
    """The gate is both conditions, so neither flag alone can hand the name away."""
    locals_source = strip_comments(CLOUDFRONT_TF.read_text())
    match = re.search(r"serves_public_name\s*=\s*(.+)", locals_source)
    assert match is not None, "local.serves_public_name is not defined in cloudfront.tf"
    expression = match.group(1).strip()
    assert "var.manage_dns" in expression, f"gate ignores manage_dns: {expression}"
    assert "!var.release_web_alias" in expression, f"gate ignores release_web_alias: {expression}"


def test_aliases_are_keyed_to_the_gate() -> None:
    """``aliases`` must follow the release flag, not ``manage_dns`` alone."""
    match = re.search(r"aliases\s*=\s*([^\n]+)", _ui_distribution())
    assert match is not None, "the flip_ui distribution sets no aliases argument"
    assert GATE in match.group(1), (
        f"aliases must be keyed to {GATE} so the name can be released: {match.group(1).strip()}"
    )


def test_every_viewer_certificate_field_uses_the_same_gate() -> None:
    """A custom certificate is only legal alongside an alias — they cannot diverge."""
    certificate = hcl_block(_ui_distribution(), "viewer_certificate")
    offenders = [line.strip() for line in certificate.splitlines() if "?" in line and GATE not in line]
    assert not offenders, (
        "every conditional in viewer_certificate must key to "
        f"{GATE}; CloudFront rejects a custom certificate on a distribution with no "
        f"alias, and the rejection lands during the cutover apply: {offenders}"
    )


def test_certificate_resource_stays_gated_on_manage_dns() -> None:
    """Releasing the alias must not destroy the certificate, or rollback needs a re-issue."""
    source = strip_comments(CLOUDFRONT_TF.read_text())
    block = hcl_block(source, 'resource "aws_acm_certificate" "flip_cloudfront"')
    match = re.search(r"count\s*=\s*([^\n]+)", block)
    assert match is not None, "the CloudFront certificate is not count-gated"
    assert "release_web_alias" not in match.group(1), (
        "the certificate must survive an alias release so putting the name back is one "
        f"re-apply rather than a DNS-validation wait: {match.group(1).strip()}"
    )


def test_variable_defaults_to_false() -> None:
    """Absent means "this account keeps the name" — the safe value for every estate."""
    block = hcl_block(strip_comments(VARIABLES_TF.read_text()), 'variable "release_web_alias"')
    assert re.search(r"default\s*=\s*false", block), (
        "release_web_alias must default to false; any other default hands the public name "
        "away on the next apply that reaches production"
    )


def test_makefile_exports_the_variable_defaulting_false() -> None:
    """The make-level default has to agree with the Terraform one."""
    source = MAKEFILE.read_text()
    assert re.search(r"^RELEASE_WEB_ALIAS \?= false$", source, re.M), "Makefile must default RELEASE_WEB_ALIAS to false"
    assert "export TF_VAR_release_web_alias=$(RELEASE_WEB_ALIAS)" in source


def test_ci_carries_the_variable() -> None:
    """CI must carry it, or a laptop apply is reverted by the next CI run.

    The manifest entry is what the compose-ci-env drift guard checks; the workflow
    entries are what actually reach Terraform.
    """
    assert "RELEASE_WEB_ALIAS" in COMPOSE_CI_ENV.read_text(), (
        "RELEASE_WEB_ALIAS is missing from the compose-ci-env manifest"
    )
    for workflow in WORKFLOWS:
        assert "RELEASE_WEB_ALIAS: ${{ vars.RELEASE_WEB_ALIAS }}" in workflow.read_text(), (
            f"{workflow.name} does not pass RELEASE_WEB_ALIAS through to the composed env"
        )
