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

variable "subscription_id" {
  description = "The Azure subscription to deploy into, as a GUID. Never the CLI default."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.subscription_id))
    error_message = "subscription_id must be a subscription GUID (az account show --subscription <name> --query id -o tsv)."
  }
}

variable "location" {
  description = "Azure region for every bootstrap resource."
  type        = string
  default     = "uksouth"
}

variable "name_prefix" {
  description = "Prefix for resource names."
  type        = string
  default     = "flipaz"
}

variable "state_storage_account_name" {
  description = "Globally unique storage account name for Terraform state (3-24 lowercase letters and digits)."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]{3,24}$", var.state_storage_account_name))
    error_message = "state_storage_account_name must be 3-24 lowercase letters and digits."
  }
}

variable "create_budget" {
  description = "Create a subscription budget with email alerts. Set false if the offer rejects budgets."
  type        = bool
  default     = true
}

variable "budget_amount" {
  description = "Monthly budget in the subscription's billing currency."
  type        = number
  default     = 100
}

variable "budget_start_date" {
  description = "First day of the budget period (must be the first of a month, RFC 3339)."
  type        = string
  default     = "2026-10-01T00:00:00Z"
}

variable "alert_emails" {
  description = "Addresses that receive the budget alerts."
  type        = list(string)
  default     = []

  validation {
    condition     = !var.create_budget || length(var.alert_emails) > 0
    error_message = "alert_emails needs at least one address while create_budget is true."
  }
}
