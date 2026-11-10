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

# Names only: no credential, no account identifier. The other roots do NOT read these; they derive
# the same names from the same trust_code.
output "role_names" {
  description = "The six FLIP roles, by purpose."
  value       = local.roles
}

output "deploy_user_name" {
  description = "The deploy SERVICE user. Its private key lives outside Terraform."
  value       = snowflake_service_user.deploy.name
}
