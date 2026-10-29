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
  description = "Cluster name, and the prefix of every resource (flip-<env>-<kit>)."
  type        = string
}

variable "region" {
  description = "AWS region."
  type        = string
  default     = "eu-west-2"
}

variable "subnet_ids" {
  description = "Subnets the site supplies, in at least two availability zones (EKS places its control-plane interfaces in each); the nodes run in the first. Empty creates a network that leaves through its own NAT gateway."
  type        = list(string)
  default     = []

  validation {
    condition     = length(var.subnet_ids) == 0 || length(var.subnet_ids) >= 2
    error_message = "subnet_ids needs two or more subnets, in different availability zones."
  }
}

variable "operator_cidrs" {
  description = "Addresses the public API endpoint admits (the Makefile passes the operator's IP). The nodes reach the API server through the private endpoint."
  type        = list(string)

  validation {
    condition     = length(var.operator_cidrs) > 0 && alltrue([for c in var.operator_cidrs : can(cidrhost(c, 0)) && !startswith(c, "0.0.0.0/")])
    error_message = "operator_cidrs must name the operator's own addresses, never 0.0.0.0/0."
  }
}

variable "kubernetes_version" {
  description = "Kubernetes minor version; empty takes EKS's default."
  type        = string
  default     = ""
}

variable "instance_type" {
  description = "Node instance type. The default is the largest an AWS Free plan account may launch."
  type        = string
  default     = "m7i-flex.large"
}

variable "node_count" {
  description = "Nodes in the one node group. Two m7i-flex.large (2 vCPU, 8 GiB each) hold the trust stack with values-small-node.yaml."
  type        = number
  default     = 2
}

variable "node_disk_size" {
  description = "Root volume of each node, GiB: container images only, the trust's data is on its own EBS volumes."
  type        = number
  default     = 60
}

variable "permissions_boundary_arn" {
  description = "Permissions boundary for every role this root creates, where the account requires one. Empty sets none."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Extra tags on every resource."
  type        = map(string)
  default     = {}
}
