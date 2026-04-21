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

variable "AWS_REGION" {
  type        = string
  description = "AWS region for the dev account (same as stag/prod in practice, but parameterised for flexibility)."
  default     = "eu-west-2"
}

variable "flip_user_pool_name" {
  type        = string
  description = "Dev Cognito pool name. Keep distinct from prod so a misconfigured AWS_PROFILE does not touch the wrong pool."
  default     = "flip-user-pool-dev"
}

variable "flip_cognito_client" {
  type        = string
  description = "Dev Cognito app client name."
  default     = "flip-cognito-client-dev"
}

variable "sign_in_hostname" {
  type        = string
  description = "Hostname used in the SMS invite template. Dev typically points at localhost since the app runs in Docker Compose."
  default     = "localhost"
}

variable "flip_cognito_admin_email" {
  type        = string
  description = "Seed admin email in the dev pool. Override via TF_VAR_flip_cognito_admin_email or .env.dev."
}

variable "flip_cognito_researcher_email" {
  type        = string
  description = "Seed researcher email in the dev pool. Set to an empty string to skip creating the researcher user."
  default     = ""
}

variable "ADMIN_USER_PASSWORD" {
  type        = string
  description = "Initial password set for both seed users. Read from TF_VAR_ADMIN_USER_PASSWORD; keep out of git."
  sensitive   = true
}

variable "SES_VERIFIED_EMAIL" {
  type        = string
  description = "Verified SES sender address for the dev account."
}

variable "cognito_callback_urls" {
  type        = list(string)
  description = "OAuth callback URLs for the dev Cognito app client."
  default     = ["https://localhost:443"]
}

variable "cognito_logout_urls" {
  type        = list(string)
  description = "OAuth logout URLs for the dev Cognito app client."
  default     = ["https://localhost:443"]
}
