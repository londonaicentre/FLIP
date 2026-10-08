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

variable "name_prefix" {
  description = "Prefix for resource names."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group the network resources go in."
  type        = string
}

variable "address_space" {
  description = "VNet address space (created network only)."
  type        = list(string)
  default     = ["10.42.0.0/16"]
}

variable "subnet_prefix" {
  description = "Node subnet prefix (created network only)."
  type        = string
  default     = "10.42.1.0/24"
}

variable "existing_subnet_id" {
  description = "A subnet the site supplies. When set, the module creates no network resources."
  type        = string
  default     = ""
}

variable "egress_public_ip_id" {
  description = "The bootstrap's static public IP. Used by nat mode (public_ip mode attaches it to the NIC in the root)."
  type        = string
  default     = ""
}

variable "egress_mode" {
  description = "public_ip: the static IP on the VM's NIC. nat: a NAT gateway with that IP. none: the site's own egress."
  type        = string
  default     = "public_ip"

  validation {
    condition     = contains(["public_ip", "nat", "none"], var.egress_mode)
    error_message = "egress_mode must be public_ip, nat or none."
  }
  validation {
    condition     = !(var.egress_mode == "nat" && var.existing_subnet_id != "")
    error_message = "nat mode needs a subnet this module creates; with existing_subnet_id use none (the site's egress) or public_ip."
  }
}
