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

"""Static guards on the Ark+ demo-assets bucket across the two estates (FLIP#1199).

The bucket has two mutually exclusive shapes:

* legacy, self-contained account — the bucket predates this stack, is adopted through
  ``data.aws_s3_bucket.demo_assets`` and must survive ``make destroy``; Terraform owns only
  the access edges (public-access block, OAC bucket policy, CloudFront origin + behaviours);
* LZA estate — nothing to adopt and no in-account CloudFront, so Terraform *creates* the
  bucket via ``module.flip_demo_assets_bucket`` and grants the networking account's edge
  distribution a cross-account OAC read.

What these pin is the gating, because getting it wrong is destructive in both directions: a
legacy-shaped resource left ungated on LZA would try to attach an origin to a distribution
that does not exist, and the module left ungated on legacy would try to *create* a bucket
that already exists (and whose name is owned by the old account).

Source text only — no ``terraform`` binary, no state, no credentials, so this runs on fork
PRs alongside the rest of ``tests/``.
"""

from pathlib import Path

import pytest
from tf_source import hcl_block, strip_comments

AWS_DIR = Path(__file__).resolve().parent.parent

#: Every legacy-only demo resource, as the header its block opens with. Each one touches the
#: in-account CloudFront distribution or the adopted bucket, neither of which exists on LZA.
LEGACY_ONLY_BLOCKS = (
    'data "aws_s3_bucket" "demo_assets"',
    'resource "aws_cloudfront_origin_access_control" "demo_assets"',
    'resource "aws_s3_bucket_public_access_block" "demo_assets"',
    'resource "aws_s3_bucket_policy" "demo_assets"',
    'resource "aws_cloudfront_response_headers_policy" "ark_demo_spa"',
    'resource "aws_cloudfront_response_headers_policy" "ark_demo_assets"',
)


@pytest.fixture(scope="module")
def cloudfront_tf() -> str:
    return strip_comments((AWS_DIR / "cloudfront.tf").read_text())


@pytest.fixture(scope="module")
def services_tf() -> str:
    return strip_comments((AWS_DIR / "services.tf").read_text())


@pytest.fixture(scope="module")
def module_tf() -> str:
    return strip_comments((AWS_DIR / "modules" / "flip_s3_bucket" / "main.tf").read_text())


@pytest.fixture(scope="module")
def module_vars() -> str:
    return strip_comments((AWS_DIR / "modules" / "flip_s3_bucket" / "variables.tf").read_text())


def test_the_two_shapes_are_mutually_exclusive(cloudfront_tf: str) -> None:
    """The gates are the enabled flag AND/AND-NOT the estate, so exactly one shape is ever live."""
    assert 'demo_assets_enabled = var.DEMO_ASSETS_BUCKET_NAME != ""' in cloudfront_tf
    assert "demo_assets_external = local.demo_assets_enabled && !var.lza_managed_network" in cloudfront_tf
    assert "demo_assets_managed = local.demo_assets_enabled && var.lza_managed_network" in cloudfront_tf


@pytest.mark.parametrize("header", LEGACY_ONLY_BLOCKS)
def test_legacy_only_resources_are_gated_off_on_lza(cloudfront_tf: str, header: str) -> None:
    """Each one needs the in-account CloudFront distribution, which is count 0 on LZA."""
    body = hcl_block(cloudfront_tf, header)
    assert "local.demo_assets_external ? 1 : 0" in body, (
        f"{header} must be gated on local.demo_assets_external — gating it on demo_assets_enabled "
        "alone would stand it up on an LZA estate, where there is no in-account distribution"
    )


def test_the_demo_cache_behaviours_follow_the_same_gate(cloudfront_tf: str) -> None:
    """The origin and both ordered behaviours live inside the distribution; none may use the bare flag."""
    assert "local.demo_assets_enabled ? [1] : []" not in cloudfront_tf
    assert cloudfront_tf.count("for_each = local.demo_assets_external ? [1] : []") == 3


def test_the_legacy_bucket_policy_names_the_in_account_distribution(cloudfront_tf: str) -> None:
    """With the resource legacy-only, the LZA branch of the SourceArn ternary is dead — and gone."""
    body = hcl_block(cloudfront_tf, 'resource "aws_s3_bucket_policy" "demo_assets"')
    assert '"AWS:SourceArn" = aws_cloudfront_distribution.flip_ui[0].arn' in body
    assert "lza_web_edge_distribution_arn" not in body


class TestLzaManagedBucket:
    def test_it_is_created_only_on_lza(self, services_tf: str) -> None:
        body = hcl_block(services_tf, 'module "flip_demo_assets_bucket"')
        assert "count = local.demo_assets_managed ? 1 : 0" in body
        assert 'source      = "./modules/flip_s3_bucket"' in body
        assert "bucket_name = var.DEMO_ASSETS_BUCKET_NAME" in body

    def test_it_is_access_logged_like_its_siblings(self, services_tf: str) -> None:
        body = hcl_block(services_tf, 'module "flip_demo_assets_bucket"')
        assert "logging_target_bucket = local.access_logs_bucket_name" in body
        assert "depends_on = [aws_s3_bucket_acl.flip_access_logs]" in body

    def test_it_uses_sse_s3_not_the_app_cmk(self, services_tf: str) -> None:
        # A cross-account CloudFront service principal cannot decrypt with this
        # account's CMK, and never with the AWS-managed aws/s3 key. flip_ui, the
        # other edge-read bucket, is on AES256 for the same reason.
        body = hcl_block(services_tf, 'module "flip_demo_assets_bucket"')
        assert 'sse_algorithm         = "AES256"' in body
        assert "kms_key_arn" not in body

    def test_the_edge_grant_is_prefix_scoped_and_read_only(self, services_tf: str) -> None:
        body = hcl_block(services_tf, 'module "flip_demo_assets_bucket"')
        assert '"s3:GetObject"' in body
        # No s3:ListBucket: a missing key must 403, not return an XML key listing.
        assert "ListBucket" not in body
        assert 'Resource  = "arn:aws:s3:::${var.DEMO_ASSETS_BUCKET_NAME}/ark_demo/assets/*"' in body
        assert '"AWS:SourceArn" = var.lza_web_edge_distribution_arn' in body

    def test_it_exposes_no_cors_surface(self, services_tf: str) -> None:
        # The bundles are downloaded as top-level navigations, never by fetch/XHR.
        body = hcl_block(services_tf, 'module "flip_demo_assets_bucket"')
        assert "cors_methods" not in body


class TestModuleDefaultsKeepLegacyPlansIdentical:
    """Both new module inputs default to the pre-FLIP#1199 behaviour, so no existing caller moves."""

    def test_sse_defaults_to_the_cmk_path(self, module_vars: str, module_tf: str) -> None:
        assert 'default     = "aws:kms"' in hcl_block(module_vars, 'variable "sse_algorithm"')
        sse = hcl_block(module_tf, 'resource "aws_s3_bucket_server_side_encryption_configuration" "this"')
        assert "sse_algorithm     = var.sse_algorithm" in sse
        assert 'kms_master_key_id = var.sse_algorithm == "aws:kms" ? var.kms_key_arn : null' in sse

    def test_extra_statements_default_to_none(self, module_vars: str, module_tf: str) -> None:
        assert 'default     = "[]"' in hcl_block(module_vars, 'variable "extra_bucket_policy_statements_json"')
        assert "jsondecode(var.extra_bucket_policy_statements_json)," in module_tf

    def test_no_sibling_bucket_passes_either_input(self, services_tf: str) -> None:
        for name in ("flip_model_files_uploads_bucket", "flip_fl_results_bucket", "flip_app_bundles_bucket"):
            body = hcl_block(services_tf, f'module "{name}"')
            assert "sse_algorithm" not in body
            assert "extra_bucket_policy_statements_json" not in body
