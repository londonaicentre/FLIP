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

############################
# ACM Certificate for HTTPS
############################
#
# NOTE: ACM certificate requires a two-step deployment:
# 1. First deploy only the certificate:
#    terraform apply -target=aws_acm_certificate.flip
# 2. Then deploy everything else:
#    terraform apply
# Use the Makefile targets `plan-cert` and `apply-cert`.
#
# This is because the domain_validation_options are only known after
# the certificate is created, so Terraform cannot plan the DNS records
# in a single pass.
#
# The whole chain is skipped when var.manage_dns is false (the zone-less
# first LZA bring-up, FLIP#749): DNS validation is impossible without the
# hosted zone, and an unvalidated cert cannot be attached to the ALB — which
# serves plain HTTP on the private CloudFront-VPC-origin leg instead (see the
# listeners comment in main.tf).
############################

resource "aws_acm_certificate" "flip" {
  count             = var.manage_dns ? 1 : 0
  domain_name       = var.flip_alb_subdomain
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name = "flip-certificate"
  }
}

# DNS validation record
resource "aws_route53_record" "cert_validation" {
  for_each = {
    for dvo in var.manage_dns ? tolist(aws_acm_certificate.flip[0].domain_validation_options) : [] : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  }

  allow_overwrite = true
  name            = each.value.name
  records         = [each.value.record]
  ttl             = 60
  type            = each.value.type
  zone_id         = data.aws_route53_zone.subdomain[0].zone_id
}

# Certificate validation
resource "aws_acm_certificate_validation" "flip" {
  count                   = var.manage_dns ? 1 : 0
  certificate_arn         = aws_acm_certificate.flip[0].arn
  validation_record_fqdns = [for record in aws_route53_record.cert_validation : record.fqdn]
}

output "CertificateArn" {
  description = "ACM Certificate ARN (validated; null when manage_dns is false — FLIP#749)"
  value       = var.manage_dns ? aws_acm_certificate_validation.flip[0].certificate_arn : null
}

# State migration for the counts added above (FLIP#749): keeps existing legacy
# states aligned without a manual `terraform state mv`. Safe to remove once
# every live state file has been migrated.
moved {
  from = aws_acm_certificate.flip
  to   = aws_acm_certificate.flip[0]
}

moved {
  from = aws_acm_certificate_validation.flip
  to   = aws_acm_certificate_validation.flip[0]
}
