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
  description = "Resource group the kit drop goes in."
  type        = string
}

variable "storage_account_name" {
  description = "Globally unique storage account name (3-24 lowercase letters and digits)."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]{3,24}$", var.storage_account_name))
    error_message = "storage_account_name must be 3-24 lowercase letters and digits."
  }
}

variable "allowed_subnet_ids" {
  description = "Subnets let through the storage firewall: the node's. They need the Microsoft.Storage service endpoint."
  type        = list(string)
}

variable "allowed_ips" {
  description = "Public IPs let through the storage firewall: the operator who uploads kits."
  type        = list(string)
  default     = []
}

variable "reader_principal_id" {
  description = "The node's managed identity, which downloads its kit."
  type        = string
}

variable "uploader_principal_id" {
  description = "The operator's Entra object id, which uploads kits."
  type        = string
}
