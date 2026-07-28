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
  description = "AWS region of the LZA workload account (the LZA home region)."
  default     = "eu-west-2"
}

variable "lza_vpc_name" {
  type        = string
  description = <<-EOT
    Name tag of the LZA-provisioned workload VPC to deploy into. The LZA
    "prod" VPC template names it "<AcceleratorPrefix>-<HomeRegion>-prod",
    identically in every Workloads/Prod OU account — so the default holds
    for both FLIPProduction and any future test account cut from the same
    template.
  EOT
  default     = "AWSAccelerator-eu-west-2-prod"
}

variable "enable_web_leg" {
  type        = bool
  description = <<-EOT
    Create the web leg (internal ALB + e2e-web service + alb_dns_name SSM
    parameter). Default false because an ALB requires subnets in >= 2 AZs
    and the LZA prod VPC template is single-AZ today — flip to true once the
    multi-AZ template change lands in londonaicentre/lza (README open
    question 2). The FL leg is unaffected and applies regardless.
  EOT
  default     = false
}

variable "fl_nlb_host_num" {
  type        = number
  description = <<-EOT
    Host number (cidrhost index) assigned to the FL NLB's static private IP
    in each app subnet via subnet_mapping — e.g. 250 in 10.12.0.0/24 gives
    10.12.0.250. Assigned (not discovered) so the IPs are known at plan
    time and stable by construction; keep it high and clear of the subnet's
    organically-allocated low range. App subnets are /24 in the LZA prod
    template, so any value 4..254 is addressable.
  EOT
  default     = 250
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
