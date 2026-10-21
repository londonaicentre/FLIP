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

variable "organization_name" {
  description = "Snowflake organization name. Supplied at apply time; never committed."
  type        = string
}

variable "account_name" {
  description = "Snowflake account name inside the organization. Supplied at apply time; never committed."
  type        = string
}

variable "platform_role" {
  description = <<-EOT
    The role the break-glass operator assumes. ACCOUNTADMIN is required for the account parameter,
    the resource monitor and the account policy attachments; SECURITYADMIN is enough for the roles,
    the deploy user and the role hierarchy, so a site that splits the apply may run the security
    half under the lesser role. Supplied at apply time so no privileged role name is defaulted here.
  EOT
  type        = string
}

variable "platform_warehouse" {
  description = "Warehouse the provider uses for its own queries. Any small warehouse the operator can use."
  type        = string
  default     = "COMPUTE_WH"
}

variable "authenticator" {
  description = <<-EOT
    How the break-glass PERSON user authenticates. USERNAMEPASSWORDMFA pairs SNOWFLAKE_PASSWORD
    with the TOTP code in SNOWFLAKE_PASSCODE; EXTERNALBROWSER delegates to the account's IdP and
    needs no password in the environment. Both read every credential from the environment: this
    root accepts none as a variable.
  EOT
  type        = string
  default     = "USERNAMEPASSWORDMFA"

  validation {
    condition     = contains(["USERNAMEPASSWORDMFA", "EXTERNALBROWSER"], var.authenticator)
    error_message = "the break-glass tier is applied by a person user with MFA: USERNAMEPASSWORDMFA or EXTERNALBROWSER."
  }
}

variable "trust_code" {
  description = "Trust code from the kit (KIT=<CODE>), upper case, letters and digits only. Every object name derives from it."
  type        = string

  validation {
    condition     = can(regex("^[A-Z][A-Z0-9]{1,15}$", var.trust_code))
    error_message = "trust_code must be 2 to 16 upper-case letters or digits and start with a letter."
  }
}

variable "deploy_user_public_key" {
  description = "PEM body (no header, footer or newlines) of the deploy SERVICE user's RSA public key. The private half never reaches Terraform."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9+/=]{300,}$", var.deploy_user_public_key))
    error_message = "deploy_user_public_key must be the base64 body of an RSA public key, without the PEM header and footer."
  }
}

variable "deploy_statement_timeout_secs" {
  description = "STATEMENT_TIMEOUT_IN_SECONDS on the deploy SERVICE user: a deploy session cannot sit on a warehouse indefinitely."
  type        = number
  default     = 300
}

variable "enable_hub_egress" {
  description = "Create the external access integration that lets services reach the hub. Off by default: trial accounts exclude external network access, so the trial runs hubless."
  type        = bool
  default     = false
}

variable "hub_api_host" {
  description = "Hostname of the Central Hub API, no scheme. Reached on 443 only. Empty unless enable_hub_egress is true."
  type        = string
  default     = ""
}

variable "fl_server_endpoints" {
  description = "FL server endpoints as host:port (the FLARE or Flower edge from the kit). Each becomes one HOST_PORT value in the egress rule."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for e in var.fl_server_endpoints : can(regex("^[A-Za-z0-9.-]+:[0-9]{1,5}$", e))])
    error_message = "every fl_server_endpoints entry must be host:port."
  }
}

variable "admin_allowed_cidrs" {
  description = "CIDRs allowed to sign in to the account. Applied as an account network policy, so an empty list means no policy is attached."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for c in var.admin_allowed_cidrs : can(cidrnetmask(c))])
    error_message = "every admin_allowed_cidrs entry must be an IPv4 CIDR."
  }
}

variable "credit_quota" {
  description = "Monthly credit quota of the resource monitor. It covers warehouses only; compute pools are capped by auto-suspend and the workload root's nightly task."
  type        = number
  default     = 50
}

variable "credit_notify_triggers" {
  description = "Percentages of the credit quota that notify the account's notification users."
  type        = list(number)
  default     = [50, 75, 90]
}

variable "resource_monitor_start_timestamp" {
  description = "Start of the resource monitor's first billing window, as Snowflake accepts it (YYYY-MM-DD HH:MM) or IMMEDIATELY."
  type        = string
  default     = "IMMEDIATELY"
}

variable "platform_database_retention_days" {
  description = "Time Travel retention on the small platform-owned database that holds the event table."
  type        = number
  default     = 1
}
