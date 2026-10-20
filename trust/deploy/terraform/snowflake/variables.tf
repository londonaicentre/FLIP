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

variable "terraform_role" {
  description = "Role the provider assumes. The one-off bootstrap runs as ACCOUNTADMIN; every later run uses the trust admin role."
  type        = string
  default     = "SYSADMIN"
}

variable "terraform_warehouse" {
  description = "Warehouse the provider uses for its own queries."
  type        = string
  default     = "COMPUTE_WH"
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

variable "pool_instance_family" {
  description = "Instance family of the service compute pool."
  type        = string
  default     = "CPU_X64_S"
}

variable "job_pool_instance_family" {
  description = "Instance family of the job compute pool (dcm2niix conversions)."
  type        = string
  default     = "CPU_X64_XS"
}

variable "gpu_pool_instance_family" {
  description = "Instance family of the optional GPU compute pool."
  type        = string
  default     = "GPU_NV_S"
}

variable "pool_max_nodes" {
  description = "Maximum nodes in the service compute pool."
  type        = number
  default     = 2
}

variable "job_pool_max_nodes" {
  description = "Maximum nodes in the job compute pool."
  type        = number
  default     = 1
}

variable "gpu_pool_max_nodes" {
  description = "Maximum nodes in the optional GPU compute pool."
  type        = number
  default     = 1
}

variable "pool_auto_suspend_secs" {
  description = "Seconds of inactivity before a compute pool suspends. Resource monitors do not cover SPCS, so this is the primary cost guard."
  type        = number
  default     = 600

  validation {
    condition     = var.pool_auto_suspend_secs > 0 && var.pool_auto_suspend_secs <= 3600
    error_message = "pool_auto_suspend_secs must be between 1 and 3600: an idle pool bills every node it holds."
  }
}

variable "enable_gpu_pool" {
  description = "Create the GPU compute pool. Off by default: trial accounts have no GPU quota and the CPU fl-client is the tested path."
  type        = bool
  default     = false
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
  description = "Monthly credit quota of the resource monitor. It covers warehouses only; compute pools are capped by auto-suspend and the nightly task."
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

variable "num_fl_nets" {
  description = "Number of FL networks this trust joins. One IMAGES prefix and one fl-client service per net (#1582)."
  type        = number
  default     = 1

  validation {
    condition     = var.num_fl_nets >= 1 && var.num_fl_nets <= 10
    error_message = "num_fl_nets must be between 1 and 10."
  }
}

variable "suspend_pools_cron" {
  description = "Cron expression (Snowflake task syntax, without the timezone) for the nightly compute-pool suspend."
  type        = string
  default     = "0 22 * * *"
}

variable "suspend_pools_timezone" {
  description = "Timezone the nightly suspend task runs in."
  type        = string
  default     = "Europe/London"
}

variable "manage_services" {
  description = "Placeholder for #1582: when the SPCS service specs land, this root gains the services themselves. It changes nothing yet."
  type        = bool
  default     = false
}
