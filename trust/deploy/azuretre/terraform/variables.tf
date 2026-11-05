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

variable "tre_id" {
  type        = string
  description = "Unique TRE ID"
}

variable "tre_resource_id" {
  type        = string
  description = "Resource ID of this workspace service"
}

variable "workspace_id" {
  type        = string
  description = "Unique TRE workspace ID"
}

variable "workspace_subscription_id" {
  type        = string
  description = "The workspace's subscription, when it is not the TRE core's"
  default     = ""
}

variable "address_space" {
  type        = string
  description = "The node subnet's address space, allocated by the TRE API"

  validation {
    condition     = can(cidrhost(var.address_space, 0))
    error_message = "address_space must be a CIDR range."
  }
}

variable "flip_release" {
  type        = string
  description = "The FLIP release tag the node clones at first boot"

  validation {
    condition     = can(regex("^v[0-9]+\\.[0-9]+\\.[0-9]+$", var.flip_release))
    error_message = "flip_release must be a release tag vX.Y.Z, never a branch name."
  }
}

variable "fl_backend" {
  type        = string
  description = "FL backend the node is provisioned for"

  validation {
    condition     = contains(["nvflare", "flower"], var.fl_backend)
    error_message = "fl_backend must be nvflare or flower."
  }
}

variable "vm_size" {
  type        = string
  description = "A key of porter.yaml's custom.vm_sizes"
}

variable "fl_server_ip_addresses" {
  type        = string
  description = "The FL server's IPv4 addresses: the base64 JSON list the resource processor passes for an array property"

  validation {
    condition = try(length(jsondecode(base64decode(var.fl_server_ip_addresses))) > 0 && alltrue([
      for ip in jsondecode(base64decode(var.fl_server_ip_addresses)) :
      can(cidrhost("${ip}/32", 0)) && length(split(".", ip)) == 4
    ]), false)
    error_message = "fl_server_ip_addresses must be base64 JSON of a non-empty list of IPv4 addresses (not hostnames)."
  }
}

variable "fl_port" {
  type        = number
  description = "The kit's FL port"

  validation {
    condition     = var.fl_port >= 1 && var.fl_port <= 65535 && floor(var.fl_port) == var.fl_port
    error_message = "fl_port must be a TCP port number."
  }
}

variable "enable_shutdown_schedule" {
  type        = bool
  description = "Shut the VM down daily"
}

variable "shutdown_time" {
  type        = string
  description = "Daily shutdown time, HHmm"

  validation {
    condition     = !var.enable_shutdown_schedule || can(regex("^([01][0-9]|2[0-3])[0-5][0-9]$", var.shutdown_time))
    error_message = "shutdown_time must be HHmm when the shutdown schedule is on."
  }
}

variable "shutdown_timezone" {
  type        = string
  description = "Windows time zone name for the shutdown schedule"
}
