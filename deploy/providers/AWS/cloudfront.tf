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

############################################################
# The flip-ui SPA bucket and the CloudFront objects in this account.
#
# The networking account's edge distribution (aicentre-lza-iac) serves the UI
# from aws_s3_bucket.flip_ui through cross-account OAC, relays /api/* and
# carries its own WAF (FLIP#749). The in-account distribution went with the
# self-contained mode (FLIP#1479). The OAC, SPA function and the origin-request
# and response-headers policies below still exist on both LZA estates: unused,
# kept for a separate cleanup.
############################################################

############################
# S3 bucket for UI assets
############################

resource "aws_s3_bucket" "flip_ui" {
  bucket = var.FLIP_UI_BUCKET_NAME

  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Name = "flip-ui"
  }
}

resource "aws_s3_bucket_public_access_block" "flip_ui" {
  bucket                  = aws_s3_bucket.flip_ui.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "flip_ui" {
  bucket = aws_s3_bucket.flip_ui.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "flip_ui" {
  bucket = aws_s3_bucket.flip_ui.id

  versioning_configuration {
    status = "Enabled"
  }
}

############################
# Public Ark+ demo download assets (optional; prod only)
#
# The bucket is module.flip_demo_assets_bucket (services.tf), created when
# DEMO_ASSETS_BUCKET_NAME is set; the edge distribution reads it through
# cross-account OAC. Clearing the name on an estate that has the bucket
# proposes a destroy, which prevent_destroy turns into a loud apply failure.
############################

locals {
  demo_assets_enabled = var.DEMO_ASSETS_BUCKET_NAME != ""
}

############################
# CloudFront: OAC, function, bucket policy
############################

resource "aws_cloudfront_origin_access_control" "flip_ui" {
  name                              = "flip-ui-${var.flip_alb_subdomain}"
  description                       = "OAC for the flip-ui S3 bucket"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# SPA deep-link rewriter. Attached to the default (S3) cache behavior and
# (when the demo is enabled) the /ark_demo/* behavior — both serve the same
# s3-flip-ui origin, one real app at "/" and one second SPA bundled under
# "/ark_demo/" (see the demo-assets section above). NOT attached to /api/*,
# so /api/* responses keep their real ALB status codes.
# IMPORTANT: do not add distribution-level custom_error_response blocks
# for 403/404 — they are inherited by /api/* and would mask real error
# codes as 200 index.html (the bug this function exists to avoid).
resource "aws_cloudfront_function" "spa_rewrite" {
  # CloudFront Function names are [A-Za-z0-9-_]{1,64}; strip any other
  # character the subdomain might ever contain.
  name    = "flip-ui-spa-rewrite-${replace(var.flip_alb_subdomain, "/[^a-zA-Z0-9]/", "-")}"
  runtime = "cloudfront-js-2.0"
  comment = "Rewrite SPA deep links (non-asset) to the matching app's index.html"
  publish = true
  code    = <<-EOT
    function handler(event) {
      var uri = event.request.uri;
      // Known asset extensions pass through so missing assets 404
      // naturally. Using an allowlist (vs. "any path containing a dot")
      // avoids misrouting dotted SPA segments like /v1.2/projects/42.
      // `zip` covers the demo's model-file download bundles under
      // /ark_demo/assets/*. Without it a request whose behaviour is missing or
      // mis-provisioned falls through to the SPA rewrite and NAVIGATES THE TAB
      // TO index.html instead of 404ing — the user sees the app reload rather
      // than a failed download, which reads as a UI bug (FLIP#794 review).
      if (/\.(js|css|map|json|html|txt|png|jpe?g|gif|svg|ico|webp|avif|woff2?|ttf|eot|zip)$/i.test(uri)) {
        return event.request;
      }
      // The public Ark+ demo is a second SPA under /ark_demo/ in the same
      // bucket; its deep links must fall back to ITS OWN index.html — falling
      // back to the top-level one would silently serve the real (Cognito-
      // gated) app for a demo URL instead of a 404 or the demo shell.
      event.request.uri = uri.indexOf('/ark_demo/') === 0 ? '/ark_demo/index.html' : '/index.html';
      return event.request;
    }
  EOT
}

locals {
  # Browser-facing origin of the UI, consumed by the S3 bucket CORS rules and the
  # Cognito URLs in services.tf: the canonical subdomain when this account hosts the
  # zone, else the networking account's edge domain (FLIP#749 WP3).
  ui_origin = var.manage_dns ? "https://${var.flip_alb_subdomain}" : "https://${var.lza_web_edge_domain}"
}

# Custom origin-request policy for /api/*. The managed AllViewer policy
# forwards everything (all headers, cookies, query strings); this narrows
# to the minimum the API actually needs.
#
# Forwarded explicitly:
# - Content-Type: for JSON/multipart requests.
# - Origin: for CORS preflight.
#
# NOT in the whitelist (because CloudFront forwards it automatically and
# the CreateOriginRequestPolicy API rejects it as a reserved parameter):
# - Authorization: always forwarded to custom origins. Carries both
#   Cognito access tokens (user → API) and trust API keys
#   (TRUST_API_KEY_HEADER=Authorization).
#
# Deliberately excluded:
# - X-Internal-Service-Key: fl-server → flip-api traffic is
#   docker-network-internal on the Central Hub and never crosses
#   CloudFront.
#
# Cookies are dropped (API is JWT/stateless). Query strings pass through
# untouched because endpoints use them for filters/pagination and there's
# no central allowlist to vet against.
resource "aws_cloudfront_origin_request_policy" "flip_api" {
  name    = "flip-api-origin-request-${replace(var.flip_alb_subdomain, "/[^a-zA-Z0-9]/", "-")}"
  comment = "Least-privilege origin-request policy for /api/* on ${var.flip_alb_subdomain}"

  headers_config {
    header_behavior = "whitelist"
    headers {
      # Authorization carries the Cognito JWT — without it on the whitelist
      # CloudFront drops the header at the edge and every /api/* request
      # hits flip-api looking like an unauthenticated caller, so the
      # backend's HTTPBearer dependency returns 401 "Not authenticated".
      items = ["Authorization", "Content-Type", "Origin"]
    }
  }

  cookies_config {
    cookie_behavior = "none"
  }

  query_strings_config {
    query_string_behavior = "all"
  }
}

############################
# Response headers policy (static / SPA behavior only)
############################

# Attached to the default (S3) cache behavior only. Do NOT attach to /api/*
# — per the viewer-request-function caveat above, distribution-level or
# api-behavior-level headers can leak into API responses and break API
# consumers that do not expect them.
#
# HSTS omits `preload` deliberately: joining the HSTS preload list is a
# one-way door across every subdomain of the apex and belongs in its own
# coordinated PR, not here.
#
# CSP is ENFORCING (FLIP#417 stage 2). It shipped report-only from the
# original pen-test remediation (§4.7) so violations could surface first; no
# `report-uri` was ever configured, so there was never a violation corpus to
# wait for, and the promotion was de-risked instead by auditing the built
# bundle for inline-style sinks (see style-src below). Pinned by
# tests/test_csp_enforcing.py. Verify after deploy:
#   curl -sI https://<domain>/ | grep -i content-security-policy
# (the `-Report-Only` suffix must be absent)
resource "aws_cloudfront_response_headers_policy" "flip_ui_spa" {
  # checkov:skip=CKV_AWS_259:HSTS is sent (1y max-age, includeSubDomains); preload deliberately withheld until the domain is submitted to the browser preload list
  name    = "flip-ui-spa-${replace(var.flip_alb_subdomain, "/[^a-zA-Z0-9]/", "-")}"
  comment = "Security response headers for the flip-ui SPA at ${var.flip_alb_subdomain}"

  security_headers_config {
    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = true
      preload                    = false
      override                   = true
    }

    content_type_options {
      override = true
    }

    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }

    frame_options {
      frame_option = "DENY"
      override     = true
    }
    content_security_policy {
      content_security_policy = join(" ", [
        "default-src 'self';",
        # CSP source expressions only allow wildcards in the leftmost
        # position (`*.amazonaws.com` OK, `cognito-idp.*.amazonaws.com`
        # is not — the browser silently drops invalid entries). Pin to
        # the deployed region so we keep the allowlist tight; update if
        # the pool is moved to a different region.
        # The S3 entry is the model-file transfer origin: the SPA fetch()es
        # presigned URLs directly (model-service.ts upload POST,
        # file-service.ts config/metrics GET). flip-api presigns against
        # AWS_ENDPOINT_URL_S3 = https://${local.s3_regional_endpoint_host}
        # (locals.tf), which makes every presigned URL PATH-STYLE — bucket
        # in the path, host exactly that — so the one exact origin admits
        # both buckets and nothing else. Derived from the same local so the
        # policy cannot drift from what flip-api signs.
        "connect-src 'self' https://cognito-idp.eu-west-2.amazonaws.com https://cognito-identity.eu-west-2.amazonaws.com https://${local.s3_regional_endpoint_host};",
        "img-src 'self' data:;",
        # 'unsafe-inline' here is a deliberate, scoped regression of
        # GHSA-vp94-g35p-29w8 stage 1 (which dropped it while nothing was yet
        # enforced): `codemirror-editor-vue3` runs a styleInject() at module
        # load that creates a <style> element, so `style-src 'self'` strips the
        # cohort-query SQL editor. A `vite build` audit found that the ONLY
        # runtime style injection in the bundle — every other stylesheet is a
        # static .css file — so this buys back one dependency and nothing else.
        # Removal at source is FLIP#1200; test_csp_enforcing.py bounds this
        # directive to {'self', 'unsafe-inline'} so the carve-out cannot spread.
        "style-src 'self' 'unsafe-inline';",
        # The substantive XSS control, and the actual §4.7 remediation.
        "script-src 'self';",
        "object-src 'none';",
        "frame-ancestors 'none';",
        # Neither directive touches a current flow, and the audit behind that —
        # repeat it before trusting the claim again, the way the style-src
        # carve-out above rests on a `vite build` audit:
        #
        #   * base-uri: flip-ui/index.html carries no <base> tag.
        #   * form-action governs where a form SUBMISSION may navigate, and the
        #     SPA never navigates one. `grep -rn "<form[ >]" flip-ui/src` finds
        #     no raw <form> outside tests; the elements that do render come from
        #     vee-validate's <Form> (18 components), which preventDefault()s the
        #     submit event and hands the values to a JS handler, and none of them
        #     sets an action attribute. The one multipart POST — model-service.ts
        #     building FormData for a presigned S3 upload — goes out through
        #     fetch(), so connect-src governs it, not form-action.
        #
        # 'self' rather than the demo's 'none' so a same-origin POST stays
        # possible; it still blocks a form retargeted at an external host.
        # test_csp_enforcing.py bounds form-action to {'self', 'none'}, so this
        # cannot widen without the audit above being redone.
        "base-uri 'none';",
        "form-action 'self';",
      ])
      override = true
    }
  }
}

############################
# Response headers policy (/api/* responses)
############################

# Security headers for /api/* responses. Only includes headers that are
# safe and beneficial for API JSON responses. CSP is intentionally
# excluded — it belongs on the SPA (HTML) only, and applying it here
# would leak CSP policy into every API response for no security benefit.
resource "aws_cloudfront_response_headers_policy" "flip_api" {
  # checkov:skip=CKV_AWS_259:HSTS is sent (1y max-age, includeSubDomains); preload deliberately withheld until the domain is submitted to the browser preload list
  name    = "flip-api-${replace(var.flip_alb_subdomain, "/[^a-zA-Z0-9]/", "-")}"
  comment = "Security response headers for /api/* at ${var.flip_alb_subdomain}"

  security_headers_config {
    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = true
      preload                    = false
      override                   = true
    }

    content_type_options {
      override = true
    }

    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }

    frame_options {
      frame_option = "DENY"
      override     = true
    }
  }
}

resource "aws_s3_bucket_policy" "flip_ui" {
  bucket = aws_s3_bucket.flip_ui.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowCloudFrontOAC"
        Effect    = "Allow"
        Principal = { Service = "cloudfront.amazonaws.com" }
        Action    = "s3:GetObject"
        Resource  = "${aws_s3_bucket.flip_ui.arn}/*"
        Condition = {
          StringEquals = {
            "AWS:SourceArn" = var.lza_web_edge_distribution_arn
          }
        }
      },
      {
        Sid       = "DenyHTTP"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.flip_ui.arn,
          "${aws_s3_bucket.flip_ui.arn}/*",
        ]
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      }
    ]
  })
}

# Server access logging for the flip-ui bucket. Depends on the
# LogDelivery ACL grant so AWS doesn't reject the logging-enable call
# on first apply.
resource "aws_s3_bucket_logging" "flip_ui" {
  bucket = aws_s3_bucket.flip_ui.id

  target_bucket = aws_s3_bucket.flip_access_logs.id
  target_prefix = "${aws_s3_bucket.flip_ui.bucket}/"

  depends_on = [aws_s3_bucket_acl.flip_access_logs]
}

############################
# Outputs
############################

output "FlipUiBucketName" {
  description = "S3 bucket holding the UI static assets"
  value       = aws_s3_bucket.flip_ui.bucket
}
