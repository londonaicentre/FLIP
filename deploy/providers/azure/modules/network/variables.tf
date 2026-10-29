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
  description = "A subnet the site supplies. When set, the module creates no network resources: outbound traffic follows the site's own routes, and the subnet must carry the Microsoft.Storage service endpoint the kit drop's firewall needs."
  type        = string
  default     = ""
}
