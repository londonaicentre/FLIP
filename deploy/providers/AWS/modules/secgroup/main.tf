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

resource "aws_security_group" "security_group" {
  name        = var.name
  vpc_id      = var.vpc_id
  description = var.description

  # Egress is managed ENTIRELY through this inline attribute — never attach an
  # `aws_security_group_rule` with `type = "egress"` to a security group built by
  # this module. `egress` is an attributes-as-blocks field, so any explicit value
  # (including `[]`) is authoritative rather than "unmanaged": mixing it with
  # standalone rule resources is the combination the AWS provider documents as
  # causing "rule conflicts, perpetual differences, and rules being overwritten".
  # Concretely, after an apply the refresh reads the standalone rules back into
  # this attribute, the config still says `[]`, and the next plan revokes the
  # whole allowlist — including the SSM rules that keep the host reachable. It
  # never converges.
  #
  # Ingress deliberately uses the opposite mechanism (standalone
  # `aws_security_group_rule` resources, this resource's `ingress` attribute never
  # set), which is what lets externally attached ingress rules — e.g. the
  # CloudFront VPC-origin rule in cloudfront.tf — coexist safely. The asymmetry is
  # intentional: pick one mechanism per direction and do not cross them.
  egress = var.block_all_outbound ? [
    for r in var.egress_rules : {
      from_port        = r.port
      to_port          = r.port
      protocol         = r.protocol
      description      = r.description
      cidr_blocks      = r.cidr_blocks == null ? [] : r.cidr_blocks
      ipv6_cidr_blocks = []
      prefix_list_ids  = r.prefix_list_ids == null ? [] : r.prefix_list_ids
      security_groups  = r.source_security_group_id == null ? [] : [r.source_security_group_id]
      self             = false
    }
    ] : [{
      from_port        = 0
      to_port          = 0
      protocol         = "-1"
      cidr_blocks      = ["0.0.0.0/0"]
      ipv6_cidr_blocks = ["::/0"]
      description      = "allow all outbound"
      prefix_list_ids  = []
      security_groups  = []
      self             = false
  }]
}

resource "aws_security_group_rule" "ingress" {
  for_each          = { for rule in var.ingress_rules : rule.port => rule }
  security_group_id = aws_security_group.security_group.id
  type              = "ingress"
  # Exactly one of these three is non-null on any given rule — the variable
  # validation in variables.tf enforces that at plan time, so the other two pass
  # through as null and the provider ignores them. There is deliberately no
  # fall-open to 0.0.0.0/0 for a rule that names no source: such a rule is
  # rejected rather than silently opened to the world.
  cidr_blocks              = each.value.cidr_blocks
  source_security_group_id = each.value.source_security_group_id
  prefix_list_ids          = each.value.prefix_list_ids
  protocol                 = "tcp"
  from_port                = each.value.port
  to_port                  = each.value.port
  description              = each.value.description
}
