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
  description = "Permanent password for the seeded Cognito admin user. Read from TF_VAR_ADMIN_USER_PASSWORD; keep out of git. Optional — leave unset and Cognito will generate a one-time temporary password and email it via the invite template (first sign-in forces a change). Dev deployments typically set this so flip-api integration tests can sign in as admin via USER_PASSWORD_AUTH."
  default     = null
  sensitive   = true
}

variable "RESEARCHER_USER_PASSWORD" {
  type        = string
  description = "Permanent password for the seeded Cognito researcher user. Distinct variable from ADMIN_USER_PASSWORD so the two seed identities never share a credential. Optional — defaults to null so Cognito generates a one-time temporary password and emails it via the invite template (first sign-in forces a change)."
  default     = null
  sensitive   = true
}

variable "SES_VERIFIED_EMAIL" {
  type        = string
  description = "Verified SES sender address for the dev account."
}

variable "cognito_callback_urls" {
  type        = list(string)
  description = "OAuth callback URLs for the dev Cognito app client. Doubles as the source for flip-api's CORS allowlist (see flip_api/utils/cognito_helpers.py:get_cors_allowed_origins), so every UI origin that calls the API in dev must be listed here. Cognito only accepts http:// for the localhost host."
  default     = ["https://localhost:443", "http://localhost:44357"]
}

variable "cognito_logout_urls" {
  type        = list(string)
  description = "OAuth logout URLs for the dev Cognito app client."
  default     = ["https://localhost:443"]
}

variable "cognito_mfa_configuration" {
  type        = string
  description = "Pool-level MFA mode for the dev pool. Defaults to OFF so local devs aren't challenged by stale per-user TOTP enrolments at sign-in; the stag/prod roots leave this unset and inherit the module default of OPTIONAL."
  default     = "OFF"
}
