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

variable "user_pool_name" {
  type        = string
  description = "Name of the Cognito user pool (e.g. \"flip-user-pool\", \"flip-user-pool-dev\")."
}

variable "client_name" {
  type        = string
  description = "Name of the Cognito app client."
}

variable "sign_in_hostname" {
  type        = string
  description = "Hostname interpolated into the SMS invite template (\"Sign in at https://{host}\"). Typically the ALB subdomain in prod or \"localhost\" in dev."
}

variable "callback_urls" {
  type        = list(string)
  description = "OAuth callback URLs for the app client."
  default     = ["https://localhost:443"]
}

variable "logout_urls" {
  type        = list(string)
  description = "OAuth logout URLs for the app client."
  default     = ["https://localhost:443"]
}

variable "admin_email" {
  type        = string
  description = "Email of the seed admin user."
}

variable "researcher_email" {
  type        = string
  description = "Email of the seed researcher user; pass an empty string to skip creating one."
  default     = ""
}

variable "seed_user_password" {
  type        = string
  description = "Initial password set for the seed users. Both admin and researcher share this value."
  sensitive   = true
}

variable "templates_dir" {
  type        = string
  description = "Path to the directory containing invite.html, password_reset_code.html and password_reset_link.html. Callers typically pass $${path.module}/templates/cognito."
}
