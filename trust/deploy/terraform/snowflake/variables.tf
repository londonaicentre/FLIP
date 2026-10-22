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
  description = "The role the provider assumes. Always the workload role, FLIP_<CODE>_DEPLOY: this root needs nothing more."
  type        = string

  validation {
    condition     = can(regex("^FLIP_[A-Z0-9]+_DEPLOY$", var.terraform_role))
    error_message = "the workload root is applied as FLIP_<CODE>_DEPLOY. ACCOUNTADMIN, SECURITYADMIN and SYSADMIN belong to the platform root."
  }
}

variable "terraform_warehouse" {
  description = <<-EOT
    Warehouse the provider's session uses. Leave null (the default): everything this root does is
    DDL, grants and SHOW commands, which need no warehouse, and the deploy role's own warehouse,
    FLIP_<CODE>_WH, is created BY this root, so naming it breaks the first plan ("The requested
    warehouse does not exist or not authorized").
  EOT
  type        = string
  default     = null
}

variable "trust_code" {
  description = "Trust code from the kit (KIT=<CODE>), upper case, letters and digits only. Every object name derives from it."
  type        = string

  validation {
    condition     = can(regex("^[A-Z][A-Z0-9]{1,15}$", var.trust_code))
    error_message = "trust_code must be 2 to 16 upper-case letters or digits and start with a letter."
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

variable "attach_resource_monitor" {
  description = "Attach the platform tier's FLIP_<CODE>_RM to this warehouse. Needs the MODIFY grant the platform root issues; set false only when the platform root has not been applied yet."
  type        = bool
  default     = true
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
  description = "Whether the platform root created FLIP_<CODE>_HUB_EAI. This root creates no integration and no network rule: it only reports the name so #1582's specs can reference it."
  type        = bool
  default     = false
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
