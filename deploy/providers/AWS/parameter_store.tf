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

############################
# AWS Systems Manager Parameter Store
#
# Non-sensitive configuration values injected into ECS tasks at startup.
# Sensitive values (passwords, AES keys) remain in Secrets Manager (FLIP_API).
#
# Parameters are grouped under /flip/ prefix. ECS task execution role is granted
# ssm:GetParameters on arn:aws:ssm:*:*:parameter/flip/* (see iam.tf).
############################

# --- Infrastructure / service endpoints ---

resource "aws_ssm_parameter" "db_host" {
  name  = "/flip/db/host"
  type  = "String"
  value = module.flip_db.db_instance_address

  description = "RDS PostgreSQL endpoint for the FLIP central-hub database"
}

resource "aws_ssm_parameter" "db_port" {
  name  = "/flip/db/port"
  type  = "String"
  value = tostring(var.DB_PORT)

  description = "RDS PostgreSQL port"
}

resource "aws_ssm_parameter" "db_name" {
  name  = "/flip/db/name"
  type  = "String"
  value = var.POSTGRES_DB

  description = "FLIP central-hub database name"
}

resource "aws_ssm_parameter" "db_user" {
  name  = "/flip/db/user"
  type  = "String"
  value = var.POSTGRES_USER

  description = "FLIP central-hub database username"
}

# The RDS-managed secret ARN is not itself sensitive (it's an ARN, not a password).
# flip-api and other services use this ARN to fetch the DB password at runtime.
resource "aws_ssm_parameter" "db_secret_arn" {
  name  = "/flip/db/secret_arn"
  type  = "String"
  value = module.flip_db.db_instance_master_user_secret_arn

  description = "ARN of the AWS Secrets Manager secret holding the RDS master user password"
}

# --- Cognito ---

resource "aws_ssm_parameter" "cognito_user_pool_id" {
  name  = "/flip/cognito/user_pool_id"
  type  = "String"
  value = module.cognito.user_pool_id

  description = "Cognito User Pool ID for FLIP authentication"
}

resource "aws_ssm_parameter" "cognito_app_client_id" {
  name  = "/flip/cognito/app_client_id"
  type  = "String"
  value = module.cognito.app_client_id

  description = "Cognito App Client ID for FLIP"
}

# --- S3 ---

resource "aws_ssm_parameter" "s3_flip_bucket" {
  name  = "/flip/s3/flip_bucket"
  type  = "String"
  value = var.FLIP_BUCKET_NAME

  description = "S3 bucket for FLIP model files, FL data, and app artefacts"
}

resource "aws_ssm_parameter" "s3_aicentre_bucket" {
  name  = "/flip/s3/aicentre_bucket"
  type  = "String"
  value = var.AICENTRE_BUCKET_NAME

  description = "AI Centre S3 bucket used as FL app base"
}

# --- Email (SES) ---

resource "aws_ssm_parameter" "ses_admin_email" {
  name  = "/flip/ses/admin_email"
  type  = "String"
  value = var.SES_VERIFIED_EMAIL

  description = "SES admin notification email address"
}

resource "aws_ssm_parameter" "ses_sender_email" {
  name  = "/flip/ses/sender_email"
  type  = "String"
  value = var.SES_VERIFIED_EMAIL

  description = "SES sender email address for outbound FLIP emails"
}

# --- Inter-service authentication ---
# PRIVATE_API_KEY is used for service-to-service authentication between
# the Central Hub and Trust services. Stored as SecureString since it is
# a shared secret, but accessed via SSM (not Secrets Manager) for simplicity.

resource "aws_ssm_parameter" "private_api_key_header" {
  name  = "/flip/api/private_key_header"
  type  = "SecureString"
  value = var.PRIVATE_API_KEY_HEADER

  description = "HTTP header name used for the FLIP private API key"
}

resource "aws_ssm_parameter" "private_api_key" {
  name  = "/flip/api/private_key"
  type  = "SecureString"
  value = var.PRIVATE_API_KEY

  description = "Shared secret for service-to-service authentication between Central Hub and Trust"
}

resource "aws_ssm_parameter" "internal_service_key_header" {
  name  = "/flip/api/internal_service_key_header"
  type  = "String"
  value = var.internal_service_key_header

  description = "HTTP header name used for the FLIP internal service key (fl-server to flip-api auth)"
}

# --- Trust service URLs (used by flip-api and trust-api) ---
# These are internal VPC URLs resolved once ECS service discovery or the
# Trust EC2 IP is known. Values are placeholder-defaults; update via
# `terraform apply -var` or by updating the .env file before apply.

resource "aws_ssm_parameter" "central_hub_api_url" {
  name  = "/flip/trust/central_hub_api_url"
  type  = "String"
  value = var.central_hub_api_url

  description = "Base URL of the FLIP Central Hub API reachable from Trust services"
}

resource "aws_ssm_parameter" "data_access_api_url" {
  name  = "/flip/trust/data_access_api_url"
  type  = "String"
  value = var.data_access_api_url

  description = "Internal URL of the data-access-api service"
}

resource "aws_ssm_parameter" "imaging_api_url" {
  name  = "/flip/trust/imaging_api_url"
  type  = "String"
  value = var.imaging_api_url

  description = "Internal URL of the imaging-api service"
}

# --- XNAT / PACS configuration (Trust services) ---

resource "aws_ssm_parameter" "xnat_url" {
  name  = "/flip/trust/xnat_url"
  type  = "String"
  value = var.xnat_url

  description = "XNAT server URL accessible from imaging-api"
}

resource "aws_ssm_parameter" "xnat_port" {
  name  = "/flip/trust/xnat_port"
  type  = "String"
  value = tostring(var.XNAT_PORT)

  description = "XNAT server port"
}

resource "aws_ssm_parameter" "xnat_service_user" {
  name  = "/flip/trust/xnat_service_user"
  type  = "SecureString"
  value = var.xnat_service_user

  description = "XNAT service account username for imaging-api"
}

resource "aws_ssm_parameter" "xnat_service_password" {
  name  = "/flip/trust/xnat_service_password"
  type  = "SecureString"
  value = var.xnat_service_password

  description = "XNAT service account password for imaging-api"
}

resource "aws_ssm_parameter" "pacs_id" {
  name  = "/flip/trust/pacs_id"
  type  = "String"
  value = var.pacs_id

  description = "Orthanc PACS identifier used by imaging-api"
}

resource "aws_ssm_parameter" "xnat_database_url" {
  name  = "/flip/trust/xnat_database_url"
  type  = "SecureString"
  value = var.xnat_database_url

  description = "XNAT PostgreSQL database connection URL for imaging-api"
}

# --- OMOP database (data-access-api) ---

resource "aws_ssm_parameter" "omop_db_service_name" {
  name  = "/flip/trust/omop_db_service_name"
  type  = "String"
  value = var.omop_db_service_name

  description = "Hostname or service name of the OMOP PostgreSQL instance"
}

resource "aws_ssm_parameter" "data_access_postgres_user" {
  name  = "/flip/trust/data_access_postgres_user"
  type  = "SecureString"
  value = var.data_access_postgres_user

  description = "PostgreSQL username for data-access-api OMOP database access"
}

resource "aws_ssm_parameter" "data_access_postgres_password" {
  name  = "/flip/trust/data_access_postgres_password"
  type  = "SecureString"
  value = var.data_access_postgres_password

  description = "PostgreSQL password for data-access-api OMOP database access"
}

resource "aws_ssm_parameter" "omop_postgres_db" {
  name  = "/flip/trust/omop_postgres_db"
  type  = "String"
  value = var.omop_postgres_db

  description = "OMOP PostgreSQL database name"
}
