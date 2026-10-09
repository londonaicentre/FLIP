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

variable "subscription_id" {
  description = "The Azure subscription to deploy into, as a GUID. Never the CLI default."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.subscription_id))
    error_message = "subscription_id must be a subscription GUID."
  }
}

variable "location" {
  description = "Azure region."
  type        = string
  default     = "uksouth"
}

variable "name_prefix" {
  description = "Prefix for resource names."
  type        = string
  default     = "flipaz"
}

variable "egress_public_ip_id" {
  description = "The bootstrap output egress_public_ip_id."
  type        = string
}

variable "egress_mode" {
  description = "public_ip, nat or none (validated by the network module)."
  type        = string
  default     = "public_ip"
}

variable "existing_subnet_id" {
  description = "A subnet the site supplies; empty creates the network."
  type        = string
  default     = ""
}

variable "vm_size" {
  description = "VM size; the default fits the 4-vCPU trial quota."
  type        = string
  default     = "Standard_D4s_v5"
}

variable "admin_username" {
  description = "Linux admin user (owns /opt/flip on the node)."
  type        = string
  default     = "azureuser"
}

variable "admin_ssh_public_key" {
  description = "The operator's SSH public key. Azure requires one for Linux; nothing listens for SSH from outside."
  type        = string
}

variable "os_disk_size_gb" {
  description = "OS disk size."
  type        = number
  default     = 64
}

variable "data_disk_size_gb" {
  description = "Data disk size (mounted at /opt/flip)."
  type        = number
  default     = 128
}

variable "flip_repo_url" {
  description = "Where the node clones FLIP from."
  type        = string
  default     = "https://github.com/londonaicentre/FLIP.git"
}

variable "flip_ref" {
  description = "The FLIP commit (40-hex sha) or release tag (vX.Y.Z) the node clones at first boot."
  type        = string

  validation {
    condition     = can(regex("^([0-9a-f]{40}|v[0-9]+\\.[0-9]+\\.[0-9]+)$", var.flip_ref))
    error_message = "flip_ref must be a full commit sha or a vX.Y.Z tag, never a branch name."
  }
}

variable "fl_backend" {
  description = "FL backend the node is provisioned for."
  type        = string
  default     = "nvflare"

  validation {
    condition     = contains(["nvflare", "flower"], var.fl_backend)
    error_message = "fl_backend must be nvflare or flower."
  }
}

variable "auto_shutdown_enabled" {
  description = "Shut the VM down daily (saves credit when forgotten)."
  type        = bool
  default     = true
}

variable "auto_shutdown_time" {
  description = "Daily shutdown time, HHMM."
  type        = string
  default     = "1900"
}

variable "auto_shutdown_timezone" {
  description = "Windows time-zone name for the shutdown schedule."
  type        = string
  default     = "GMT Standard Time"
}

variable "kit_drop_enabled" {
  description = "Create the kit drop: a private container the node downloads its kit from with its own identity."
  type        = bool
  default     = true
}

variable "operator_ip" {
  description = "The public IP kits are uploaded from, let through the kit drop's firewall (the Makefile looks it up)."
  type        = string
  default     = ""

  validation {
    condition     = !var.kit_drop_enabled || can(cidrhost("${var.operator_ip}/32", 0))
    error_message = "operator_ip must be an IPv4 address when the kit drop is on, or uploads are refused by its firewall."
  }
}
