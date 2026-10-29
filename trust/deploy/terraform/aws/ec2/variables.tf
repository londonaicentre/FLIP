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
  description = "Name of this trust node. Every resource is named after it, so two nodes (or this root and the hub's own trust resources) never collide in one account."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,31}$", var.name))
    error_message = "name must be 3-32 lowercase letters, digits or hyphens, starting with a letter."
  }
}

variable "region" {
  description = "AWS region of the node."
  type        = string
  default     = "eu-west-2"
}

variable "subnet_id" {
  description = "A subnet the site supplies, in its own account and VPC. When set, this root creates no VPC, internet gateway, NAT gateway or Elastic IP. Empty creates a VPC with a NAT gateway (~$40/month)."
  type        = string
  default     = ""
}

variable "instance_type" {
  description = "EC2 instance type. t3.xlarge runs the full trust stack; a smaller size suits a mock trust whose workload fits."
  type        = string
  default     = "t3.xlarge"
}

variable "ami_ssm_parameter" {
  description = "SSM parameter holding the AMI id. The default is Canonical's current Ubuntu 24.04; a GPU host names an image with the NVIDIA driver (>= 580)."
  type        = string
  default     = "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
}

variable "root_volume_size" {
  description = "Root volume size in GiB. It holds the images (the FL client alone is several GB), XNAT's archive and the OMOP data."
  type        = number
  default     = 100

  validation {
    condition     = var.root_volume_size >= 50
    error_message = "root_volume_size must be at least 50 GiB: image pulls fail below that."
  }
}

variable "public_key" {
  description = "OpenSSH public key for the ubuntu user. SSH reaches the host only through SSM Session Manager; there is no inbound rule."
  type        = string
}

variable "permissions_boundary_arn" {
  description = "Permissions boundary for the roles this root creates. A Landing Zone Accelerator estate requires one."
  type        = string
  default     = ""
}

variable "kit_bucket_name" {
  description = "S3 bucket the node may read its FL kit and the OMOP vocabulary from (read-only, the kit and vocab prefixes only). Empty grants no S3 access: the kit is then delivered out of band."
  type        = string
  default     = ""
}

variable "kit_slot" {
  description = "FL kit slot number the hub assigned this trust (FL_KIT_SLOT_NUMBER in its kit file). The S3 read is scoped to this slot's kit; required with kit_bucket_name."
  type        = number
  default     = null

  validation {
    condition     = var.kit_bucket_name == "" || (var.kit_slot != null && try(var.kit_slot >= 1, false))
    error_message = "kit_slot (the trust's FL_KIT_SLOT_NUMBER, 1 or more) is required with kit_bucket_name."
  }
}

variable "kit_kms_key_arn" {
  description = "KMS key the kit bucket's objects are encrypted with; the node may decrypt through S3 only. Empty grants no KMS access."
  type        = string
  default     = ""
}

variable "fl_server_ports" {
  description = "TCP ports of the hub's FL endpoint the node connects out to (8002 on prod, 8003 on stag)."
  type        = list(number)
  default     = [8002]
}

variable "ssm_endpoints" {
  description = "Create interface endpoints for SSM (ssm, ssmmessages, ec2messages, ~$24/month per AZ) in the node's VPC. Needed only on a subnet with no internet route to SSM. Covers Session Manager only: CloudWatch, the S3 kit and vocabulary, and the rest of the node's egress still need the site's own route."
  type        = bool
  default     = false
}

variable "schedule_enabled" {
  description = "Stop and start the node on a schedule. For mock and test trusts only: the hub shows the trust offline outside the window, and no FL job may be scheduled then."
  type        = bool
  default     = false
}

variable "schedule_start" {
  description = "EventBridge Scheduler expression that starts the node."
  type        = string
  default     = "cron(0 8 ? * MON-FRI *)"
}

variable "schedule_stop" {
  description = "EventBridge Scheduler expression that stops the node."
  type        = string
  default     = "cron(0 18 ? * MON-FRI *)"
}

variable "schedule_timezone" {
  description = "Time zone of the schedule expressions."
  type        = string
  default     = "Europe/London"
}

variable "tags" {
  description = "Extra tags for every resource."
  type        = map(string)
  default     = {}
}
