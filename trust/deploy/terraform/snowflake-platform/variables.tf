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
    How the break-glass identity authenticates. SNOWFLAKE_JWT (the default) is the just-in-time
    operator: a key-pair SERVICE user that holds no role while idle and is granted the platform role
    by an MFA-protected admin in Snowsight only for the length of an apply (README "Break-glass
    operator"); it works with any MFA method, passkeys included. USERNAMEPASSWORDMFA pairs
    SNOWFLAKE_PASSWORD with the TOTP code in SNOWFLAKE_PASSCODE for an admin enrolled with an
    authenticator app; EXTERNALBROWSER delegates to the account's IdP. Every credential comes from
    the environment: this root accepts none as a variable.
  EOT
  type        = string
  default     = "SNOWFLAKE_JWT"

  validation {
    condition     = contains(["SNOWFLAKE_JWT", "USERNAMEPASSWORDMFA", "EXTERNALBROWSER"], var.authenticator)
    error_message = "authenticator must be SNOWFLAKE_JWT (just-in-time operator), USERNAMEPASSWORDMFA or EXTERNALBROWSER."
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

variable "event_table_retention_days" {
  description = <<-EOT
    How long a log record stays in the event table (#1584). Snowflake expires nothing in an event
    table by itself, so this is enforced by the nightly DELETE task in observability.tf. Keep it
    short: the table holds every container's stdout for every trust in the account, and the
    operator's recourse for anything older is the FL job's own artefacts.
  EOT
  type        = number
  default     = 14

  validation {
    condition     = var.event_table_retention_days >= 1 && var.event_table_retention_days <= 90
    error_message = "event_table_retention_days must be between 1 and 90: logs are operational data, not a record."
  }
}

variable "event_table_prune_cron" {
  description = "Cron expression (Snowflake task syntax, without the timezone) for the event-table retention task."
  type        = string
  default     = "30 2 * * *"
}

variable "event_table_prune_timezone" {
  description = "Timezone the event-table retention task runs in."
  type        = string
  default     = "Europe/London"
}

variable "recent_errors_window_hours" {
  description = "Window of the recent-errors view. Bounded so a dashboard tile never scans the whole event table."
  type        = number
  default     = 24

  validation {
    condition     = var.recent_errors_window_hours >= 1 && var.recent_errors_window_hours <= 168
    error_message = "recent_errors_window_hours must be between 1 and 168."
  }
}

variable "cost_window_days" {
  description = "Window of the compute-pool and warehouse cost views, in days of SNOWFLAKE.ACCOUNT_USAGE history."
  type        = number
  default     = 30

  validation {
    condition     = var.cost_window_days >= 1 && var.cost_window_days <= 365
    error_message = "cost_window_days must be between 1 and 365 (ACCOUNT_USAGE keeps one year)."
  }
}

variable "warehouse_statement_timeout_secs" {
  description = "STATEMENT_TIMEOUT_IN_SECONDS on the trust warehouse: a runaway OMOP query is cancelled rather than billed."
  type        = number
  default     = 300
}

variable "warehouse_auto_suspend_secs" {
  description = "Seconds of inactivity before the trust warehouse suspends."
  type        = number
  default     = 60
}
