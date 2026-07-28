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

variable "AWS_REGION" {
  type        = string
  description = "AWS region for the e2e LZA workload account."
  default     = "eu-west-2"
}

variable "vpc_cidr" {
  type        = string
  description = <<-EOT
    CIDR for the sealed workload VPC. No default on purpose: the value must be
    allocated from the LZA IPAM and must not overlap the TGW route domain —
    it needs sign-off from the networking-account owner BEFORE apply (README
    open question 1). Set via TF_VAR_vpc_cidr in .env.e2e.
  EOT
}

variable "networking_ingress_cidrs" {
  type        = list(string)
  description = <<-EOT
    CIDRs of the networking-account ingress VPC subnets (the edge NLB / TGW
    path). Default [] so the stack applies standalone for Phase A; populate
    for Phase B — only security group rules change on re-apply. The value
    comes from the networking-account owner. Deliberately CIDR rules only:
    cross-account SG references do not work over a TGW attachment.
  EOT
  default     = []
}

variable "max_azs" {
  type        = number
  description = "Number of availability zones to spread the private subnets across."
  default     = 2
}
