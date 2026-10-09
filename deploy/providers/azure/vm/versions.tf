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

terraform {
  required_version = ">= 1.13.1"

  # Settings (resource group, storage account, container, key, subscription) are passed at
  # `terraform init` by the Makefile from the bootstrap outputs.
  backend "azurerm" {}

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

# Always the trial subscription named by the caller, never the CLI default. Compute,
# Network and Storage were registered by hand; DevTestLab backs the auto-shutdown schedule.
provider "azurerm" {
  features {}
  subscription_id = var.subscription_id
  # The kit drop has shared keys off, so the provider's storage calls must use Entra.
  storage_use_azuread             = true
  resource_provider_registrations = "none"
  resource_providers_to_register  = ["Microsoft.DevTestLab"]
}
