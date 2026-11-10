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

# The identity tier: the six FLIP roles and the deploy SERVICE user, and nothing else. It is
# applied once, by whoever administers users in the account, as a role that can create roles and
# users (USERADMIN, or ACCOUNTADMIN on an account FLIP has to itself). It creates no object that
# holds data and grants no privilege on one.
#
# No credential is written here or into a variable. The provider reads them from the environment:
#
#   export SNOWFLAKE_USER=...
#   export SNOWFLAKE_PRIVATE_KEY="$(cat <key>.p8)"   # SNOWFLAKE_JWT (default)
#   export SNOWFLAKE_PRIVATE_KEY_PASSPHRASE=...
#
# or SNOWFLAKE_TOKEN for PROGRAMMATIC_ACCESS_TOKEN, or SNOWFLAKE_PASSWORD + SNOWFLAKE_PASSCODE for
# USERNAMEPASSWORDMFA.
provider "snowflake" {
  organization_name = var.organization_name
  account_name      = var.account_name
  role              = var.identity_role
  authenticator     = var.authenticator
}
