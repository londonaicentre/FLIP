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
  type    = string
  default = "trust"
}

variable "AWS_REGION" {
  type    = string
  default = "eu-west-2"
}

variable "instance_type" {
  type    = string
  default = "t3.small"
}

variable "key_name" {
  type    = string
  default = "~/.ssh/id_rsa"
}

variable "subnet_id" {
  type = string
}

variable "security_group_ids" {
  type    = list(string)
  default = []
}

variable "create_elastic_ip" {
  type    = bool
  default = false
}

variable "XNAT_PORT" {
  type = number
}

variable "PACS_UI_PORT" {
  type = number
}

variable "iam_instance_profile_name" {
  description = "Name of an existing IAM instance profile to attach to the Trust EC2 instance"
  type        = string
}
