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

variable "organization_name" {
  description = "Snowflake organization name. Supplied at apply time; never committed."
  type        = string
}

variable "account_name" {
  description = "Snowflake account name inside the organization. Supplied at apply time; never committed."
  type        = string
}

variable "identity_role" {
  description = "The role this root is applied as. It must be able to create roles and users. Supplied at apply time so no privileged role name is defaulted here."
  type        = string
}

variable "authenticator" {
  description = "How the applying identity authenticates. Every credential comes from the environment: this root accepts none as a variable."
  type        = string
  default     = "SNOWFLAKE_JWT"

  validation {
    condition     = contains(["SNOWFLAKE_JWT", "PROGRAMMATIC_ACCESS_TOKEN", "USERNAMEPASSWORDMFA", "EXTERNALBROWSER"], var.authenticator)
    error_message = "authenticator must be SNOWFLAKE_JWT, PROGRAMMATIC_ACCESS_TOKEN, USERNAMEPASSWORDMFA or EXTERNALBROWSER."
  }
}

variable "trust_code" {
  description = "Trust code from the kit (KIT=<CODE>), upper case, letters and digits only. Every name derives from it."
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
