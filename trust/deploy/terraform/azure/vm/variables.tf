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
  description = "Prefix for resource names; it also starts the kit drop's storage account name, hence 3-13 lowercase letters and digits."
  type        = string
  default     = "flipaz"

  validation {
    condition     = can(regex("^[a-z0-9]{3,13}$", var.name_prefix))
    error_message = "name_prefix must be 3-13 lowercase letters and digits: it starts a storage account name (<prefix>kit<8 hex>, at most 24)."
  }
}

variable "existing_subnet_id" {
  description = "A subnet the site supplies; outbound traffic then follows the site's routes. Empty creates the network, which leaves through its own NAT gateway. With the kit drop on, a site subnet needs the Microsoft.Storage service endpoint, or the storage firewall refuses the node (403)."
  type        = string
  default     = ""
}

variable "image_version" {
  description = "Ubuntu 24.04 image version: \"latest\", or a pinned one from az vm image list --publisher Canonical --offer ubuntu-24_04-lts --sku server --all. Changing it replaces the VM."
  type        = string
  default     = "latest"

  validation {
    condition     = can(regex("^(latest|[0-9]+\\.[0-9]+\\.[0-9]+)$", var.image_version))
    error_message = "image_version must be \"latest\" or a version like 24.04.202510010."
  }
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
    # cidrhost alone admits IPv6 ("2001:db8::1/32" is a valid IPv6 prefix), hence the dotted quad.
    condition     = !var.kit_drop_enabled || (can(cidrhost("${var.operator_ip}/32", 0)) && length(split(".", var.operator_ip)) == 4)
    error_message = "operator_ip must be an IPv4 address when the kit drop is on, or uploads are refused by its firewall. The Makefile looks it up from api.ipify.org: if that failed, pass OPERATOR_IP=<your public IPv4>."
  }
}
