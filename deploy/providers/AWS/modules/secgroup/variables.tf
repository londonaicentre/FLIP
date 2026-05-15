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

variable "name" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "description" {
  type = string
}

variable "ingress_rules" {
  # Exactly one source selector per rule: `cidr_blocks` OR
  # `source_security_group_id` OR `prefix_list_ids` OR neither (falls back to
  # 0.0.0.0/0). Setting multiple is rejected at plan time by the validation
  # block below — AWS also rejects multi-source rules at apply, but failing
  # at plan gives a focused error rather than an opaque AWS API failure.
  type = list(object({
    port                     = number
    description              = string
    cidr_blocks              = optional(list(string))
    source_security_group_id = optional(string)
    prefix_list_ids          = optional(list(string))
  }))

  validation {
    condition = alltrue([
      for r in var.ingress_rules :
      length([
        for s in [r.cidr_blocks, r.source_security_group_id, r.prefix_list_ids] : s
        if s != null
      ]) <= 1
    ])
    error_message = "Each ingress rule must set at most one of cidr_blocks, source_security_group_id, prefix_list_ids (multi-source rules are invalid AWS config)."
  }
}

variable "block_all_outbound" {
  type    = bool
  default = false
}
