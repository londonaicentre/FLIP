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
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "= 4.57.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "= 4.1.0"
    }
  }

  # Configured by porter.yaml's backendConfig: the TRE's own state storage.
  backend "azurerm" {}
}

# As AzureTRE's Guacamole VM: the workspace may sit in another subscription than the TRE core.
provider "azurerm" {
  subscription_id = coalesce(var.workspace_subscription_id, data.azurerm_client_config.current.subscription_id)

  features {
    virtual_machine {
      skip_shutdown_and_force_delete = true
      delete_os_disk_on_deletion     = true
    }
  }
  storage_use_azuread = true
}

provider "azurerm" {
  alias = "core"
  features {}
}
