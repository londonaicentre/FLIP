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

"""Guards on the CloudFront Content-Security-Policy headers.

The original pen-test finding (§4.7) was that CSP allowed ``'unsafe-inline'``, neutralising its
XSS protection. The remediation removed it from ``script-src``/``default-src``, but the policy
then shipped as ``Content-Security-Policy-Report-Only`` — which reports and blocks nothing — for
long enough to become its own finding (FLIP#417). This suite pins the promoted state so neither
half can silently regress:

* the SPA policy must ship as an **enforcing** ``content_security_policy`` block, and the
  ``Content-Security-Policy-Report-Only`` custom header must be gone entirely;
* ``'unsafe-inline'``/``'unsafe-eval'`` must never appear in ``script-src`` or ``default-src`` —
  that is the pen-test finding itself, and no amount of "fixing a broken page" justifies it;
* the standard hardening directives stay present.

``style-src`` is bounded rather than pinned. It currently retains ``'unsafe-inline'`` for one
dependency (the CodeMirror wrapper on the cohort-query page injects a ``<style>`` element at module
load — FLIP#1200); tightening it to ``'self'`` is an improvement this suite must not block, but the
carve-out must not *spread* either, so the test allows any subset of ``{'self', 'unsafe-inline'}``
and nothing beyond it. The unsafe-token check is asserted per directive, so relaxing ``script-src``
fails here even if ``style-src`` is relaxed in the same edit.

Parsing is the same credential-free ``read_text()`` + ``re`` approach as the rest of this suite —
no ``terraform`` binary, no state, no credentials — so it runs on fork PRs.
"""

import re
from pathlib import Path

import pytest
from hcl_helpers import hcl_block

AWS_PROVIDER_DIR = Path(__file__).resolve().parent.parent
CLOUDFRONT_TF = AWS_PROVIDER_DIR / "cloudfront.tf"

# Policies that must ship enforcing, by Terraform resource name.
ENFORCING_POLICIES = ("flip_ui_spa", "ark_demo_spa")

UNSAFE_TOKENS = ("'unsafe-inline'", "'unsafe-eval'")

# The only sources style-src may carry while the FLIP#1200 carve-out stands.
STYLE_SRC_ALLOWED = {"'self'", "'unsafe-inline'"}


def _policy_block(hcl: str, resource_name: str) -> str:
    header = f'resource "aws_cloudfront_response_headers_policy" "{resource_name}"'
    assert header in hcl, f"no aws_cloudfront_response_headers_policy resource named {resource_name!r}"
    return hcl_block(hcl, header)


def _directive(policy_body: str, name: str) -> str:
    """Return the source expression of one CSP directive, read from its quoted ``"name ...;"`` entry."""
    uncommented = "\n".join(line for line in policy_body.splitlines() if not line.lstrip().startswith("#"))
    match = re.search(r'"' + re.escape(name) + r'\s+([^"]*?);"', uncommented)
    assert match, f"CSP directive {name!r} not found"
    return match.group(1).strip()


@pytest.fixture(scope="module")
def hcl() -> str:
    return CLOUDFRONT_TF.read_text(encoding="utf-8")


def test_report_only_header_is_gone(hcl: str) -> None:
    """The report-only header must not reappear anywhere in the distribution config."""
    assert "Content-Security-Policy-Report-Only" not in hcl, (
        "CSP has regressed to report-only, which blocks nothing. "
        "The enforcing policy belongs in security_headers_config.content_security_policy "
        "(FLIP#417)."
    )


@pytest.mark.parametrize("resource_name", ENFORCING_POLICIES)
def test_policy_ships_enforcing(hcl: str, resource_name: str) -> None:
    body = _policy_block(hcl, resource_name)
    assert "content_security_policy {" in body, (
        f"{resource_name} does not ship an enforcing content_security_policy block"
    )


@pytest.mark.parametrize("resource_name", ENFORCING_POLICIES)
@pytest.mark.parametrize("directive", ["script-src", "default-src"])
def test_script_and_default_src_have_no_unsafe_tokens(hcl: str, resource_name: str, directive: str) -> None:
    """The pen-test finding itself (§4.7). Never relax these to fix a rendering bug."""
    value = _directive(_policy_block(hcl, resource_name), directive)
    for token in UNSAFE_TOKENS:
        assert token not in value, (
            f"{resource_name}: {directive} contains {token}, re-opening pen-test finding §4.7. "
            f"Fix the offending code at source, or widen a different directive, instead."
        )


@pytest.mark.parametrize("resource_name", ENFORCING_POLICIES)
def test_style_src_carve_out_cannot_spread(hcl: str, resource_name: str) -> None:
    """style-src may tighten toward 'self' freely, but may not admit any source beyond the carve-out."""
    sources = set(_directive(_policy_block(hcl, resource_name), "style-src").split())
    assert sources <= STYLE_SRC_ALLOWED, (
        f"{resource_name}: style-src carries {sorted(sources - STYLE_SRC_ALLOWED)} beyond the "
        f"FLIP#1200 carve-out ({sorted(STYLE_SRC_ALLOWED)}). Fix the offending styles at source."
    )


@pytest.mark.parametrize("resource_name", ENFORCING_POLICIES)
@pytest.mark.parametrize(
    ("directive", "expected"),
    [("object-src", "'none'"), ("frame-ancestors", "'none'"), ("base-uri", "'none'")],
)
def test_hardening_directives_present(hcl: str, resource_name: str, directive: str, expected: str) -> None:
    assert _directive(_policy_block(hcl, resource_name), directive) == expected


@pytest.mark.parametrize("resource_name", ENFORCING_POLICIES)
def test_form_action_is_not_a_wildcard(hcl: str, resource_name: str) -> None:
    """'self' (real app) or 'none' (demo) — never a wildcard or an external host."""
    value = _directive(_policy_block(hcl, resource_name), "form-action")
    assert value in ("'self'", "'none'"), f"{resource_name}: unexpected form-action {value!r}"


def test_app_connect_src_admits_the_model_file_origin(hcl: str) -> None:
    """Enforcing connect-src must admit the S3 origin the SPA transfers model files to and from.

    The SPA ``fetch()``es presigned URLs directly (upload POST, config/metrics GET). flip-api presigns
    them against ``AWS_ENDPOINT_URL_S3``, derived from ``local.s3_regional_endpoint_host``, which makes
    them path-style on exactly that host — so the policy must reference the same local, not a
    hand-copied host (drift) or a ``*.s3.amazonaws.com`` wildcard (every bucket on the service).
    Report-only never blocked this; enforcement is what turns the omission into a failed upload.
    """
    sources = _directive(_policy_block(hcl, "flip_ui_spa"), "connect-src").split()
    assert "https://${local.s3_regional_endpoint_host}" in sources, (
        "flip_ui_spa: connect-src does not admit the regional S3 endpoint flip-api presigns model-file "
        "URLs against; enforcing this policy blocks model-file upload and download in the SPA."
    )
    assert not any(source.startswith("https://*.") for source in sources), (
        f"flip_ui_spa: connect-src carries a wildcard host: {sources}"
    )


def test_demo_policy_stays_stricter_than_the_app(hcl: str) -> None:
    """The demo has no CodeMirror dependency, so it must not inherit the app's style-src carve-out."""
    demo = _policy_block(hcl, "ark_demo_spa")
    assert "'unsafe-inline'" not in _directive(demo, "style-src")
    assert _directive(demo, "connect-src") == "'none'"
