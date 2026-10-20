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
# Humans sign in with MFA and a password that meets this policy; the deploy identity is a SERVICE
# user and has no password at all. The policy exists so that an account that later grows a human
# user inherits it rather than the Snowflake default.
resource "snowflake_password_policy" "trust" {
  database = snowflake_database.trust.name
  schema   = snowflake_schema.trust["app"].name
  name     = local.password_policy

  min_length           = 14
  max_length           = 256
  min_upper_case_chars = 1
  min_lower_case_chars = 1
  min_numeric_chars    = 1
  min_special_chars    = 1
  max_age_days         = 365
  max_retries          = 5
  lockout_time_mins    = 30
  history              = 5

  comment = "FLIP ${local.code}: account password policy."
}

resource "snowflake_account_password_policy_attachment" "trust" {
  password_policy = snowflake_password_policy.trust.fully_qualified_name
}

resource "snowflake_session_policy" "trust" {
  database = snowflake_database.trust.name
  schema   = snowflake_schema.trust["app"].name
  name     = local.session_policy

  session_idle_timeout_mins    = 60
  session_ui_idle_timeout_mins = 30

  comment = "FLIP ${local.code}: idle sessions expire."
}

# Person users only: a SERVICE user has no interactive session to time out, and the provider
# refuses both flags at once, so the service-user half is deliberately left unattached.
resource "snowflake_account_session_policy_attachment" "trust" {
  session_policy_name  = snowflake_session_policy.trust.fully_qualified_name
  for_all_person_users = true
}
