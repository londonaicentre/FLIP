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

# Credential-free: every azurerm call is mocked, so this runs in CI with no Azure account.

mock_provider "azurerm" {}

variables {
  subscription_id            = "00000000-0000-0000-0000-000000000000"
  state_storage_account_name = "flipaztfstatetest01"
  alert_emails               = ["ops@example.invalid"]
}

run "state_account_is_entra_only" {
  command = plan

  assert {
    condition     = azurerm_storage_account.state.shared_access_key_enabled == false
    error_message = "State storage must not allow shared-key access."
  }
  assert {
    condition     = azurerm_storage_account.state.min_tls_version == "TLS1_2"
    error_message = "State storage must require TLS 1.2."
  }
  assert {
    condition     = azurerm_storage_account.state.allow_nested_items_to_be_public == false
    error_message = "State storage must not allow public blobs."
  }
}

run "egress_ip_is_static_standard" {
  command = plan

  assert {
    condition     = azurerm_public_ip.egress.sku == "Standard" && azurerm_public_ip.egress.allocation_method == "Static"
    error_message = "The outbound IP must be a Standard, Static public IP so it survives node rebuilds."
  }
}

run "budget_is_on_by_default" {
  command = plan

  assert {
    condition     = length(azurerm_consumption_budget_subscription.trial) == 1
    error_message = "The budget alert is created by default."
  }
}

run "budget_is_optional" {
  command = plan

  variables {
    create_budget = false
    alert_emails  = []
  }

  assert {
    condition     = length(azurerm_consumption_budget_subscription.trial) == 0
    error_message = "create_budget=false must skip the budget (Free Trial offers may reject it)."
  }
}

run "rejects_a_non_guid_subscription" {
  command = plan

  variables {
    subscription_id = "KCL-IT-SVD"
  }

  expect_failures = [var.subscription_id]
}

run "budget_needs_an_email" {
  command = plan

  variables {
    alert_emails = []
  }

  expect_failures = [var.alert_emails]
}

run "state_has_soft_delete" {
  command = plan

  assert {
    condition     = azurerm_storage_account.state.blob_properties[0].delete_retention_policy[0].days >= 7 && azurerm_storage_account.state.blob_properties[0].container_delete_retention_policy[0].days >= 7
    error_message = "State blobs and containers must be recoverable for at least 7 days after deletion."
  }
}
