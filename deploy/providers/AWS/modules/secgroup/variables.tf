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
  # Exactly one non-empty source selector per rule: `cidr_blocks` OR
  # `source_security_group_id` OR `prefix_list_ids`. Setting several, none, or
  # an empty one is rejected at plan time by the validation block below. There
  # is deliberately no "none set" fallback to 0.0.0.0/0 — an inbound rule that
  # names no source is a mistake, and silently opening it to the world is the
  # worst possible reading of that mistake. Every current caller passes an
  # explicit selector or an empty rule list, so this is a zero-diff tightening.
  type = list(object({
    port                     = number
    description              = string
    cidr_blocks              = optional(list(string))
    source_security_group_id = optional(string)
    prefix_list_ids          = optional(list(string))
  }))

  validation {
    # `try(length(s), 1) > 0` rejects an empty list too: counting only non-null
    # would let `cidr_blocks = []` pass the plan and then fail at apply, which
    # contradicts the promise that a source-less rule is caught at plan time.
    # The `try` covers `source_security_group_id`, a string with no length here.
    condition = alltrue([
      for r in var.ingress_rules :
      length([
        for s in [r.cidr_blocks, r.source_security_group_id, r.prefix_list_ids] : s
        if s != null && try(length(s), 1) > 0
      ]) == 1
    ])
    error_message = "Each ingress rule must set exactly one non-empty source: cidr_blocks, source_security_group_id, or prefix_list_ids."
  }
}

variable "block_all_outbound" {
  type    = bool
  default = false
}

variable "egress_rules" {
  # Exactly one non-empty destination selector per rule: `cidr_blocks` OR
  # `source_security_group_id` OR `prefix_list_ids`. Same rule as ingress_rules
  # above, and likewise no fallback to 0.0.0.0/0 — a destination-less rule is a
  # mistake, not an intentional open rule.
  #
  # Only consumed when `block_all_outbound = true`; see the `egress` attribute in
  # main.tf for why these are rendered inline rather than as standalone rules.
  type = list(object({
    port                     = number
    protocol                 = optional(string, "tcp")
    description              = string
    cidr_blocks              = optional(list(string))
    source_security_group_id = optional(string)
    prefix_list_ids          = optional(list(string))
  }))
  default = []

  validation {
    condition = alltrue([
      for r in var.egress_rules :
      length([
        for s in [r.cidr_blocks, r.source_security_group_id, r.prefix_list_ids] : s
        if s != null && try(length(s), 1) > 0
      ]) == 1
    ])
    error_message = "Each egress rule must set exactly one non-empty destination: cidr_blocks, source_security_group_id, or prefix_list_ids."
  }

  validation {
    # An EC2 security-group rule's identity is (direction, protocol, port range,
    # destination) — the description is a mutable annotation, not part of the key
    # (hence the separate UpdateSecurityGroupRuleDescriptionsEgress API). Two
    # rules that differ only by description are the SAME rule to AWS, and the
    # second one fails the apply with InvalidPermission.Duplicate. Catching it
    # here turns a nondeterministic, never-converging apply into a plan-time
    # error naming the offending tuple.
    condition = length(distinct([
      for r in var.egress_rules :
      format("%s/%d/%s/%s/%s",
        r.protocol,
        r.port,
        join(",", sort(r.cidr_blocks == null ? [] : r.cidr_blocks)),
        r.source_security_group_id == null ? "" : r.source_security_group_id,
        join(",", sort(r.prefix_list_ids == null ? [] : r.prefix_list_ids)),
      )
    ])) == length(var.egress_rules)
    error_message = "Two or more egress rules resolve to the same (protocol, port, destination). AWS treats those as one rule regardless of description and rejects the duplicates — collapse them into a single rule whose description covers the combined rationale."
  }
}
