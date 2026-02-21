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
  egress = var.block_all_outbound ? [] : [{
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
  for_each                 = { for rule in var.ingress_rules : rule.port => rule }
  security_group_id        = aws_security_group.security_group.id
  type                     = "ingress"
  cidr_blocks              = each.value.source_security_group_id == null ? ["0.0.0.0/0"] : null
  source_security_group_id = each.value.source_security_group_id != null ? each.value.source_security_group_id : null
  protocol                 = "tcp"
  from_port                = each.value.port
  to_port                  = each.value.port
  description              = each.value.description
}
