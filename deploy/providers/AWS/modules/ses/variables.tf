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

variable "sender_email" {
  type        = string
  description = "Email address that SES verifies as the FLIP sender identity."
}

variable "templates_dir" {
  type        = string
  description = "Path to the directory containing the flip-* .html/.txt template pairs."
}

variable "template_name_prefix" {
  type        = string
  description = "Optional prefix for SES template names. Set on environments that share an AWS account with prod so the dev templates do not collide with the prod ones. Leave empty for the prod environment to preserve existing template names."
  default     = ""
}
