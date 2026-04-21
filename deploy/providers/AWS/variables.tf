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

variable "AWS_REGION" {
  type = string
}

variable "VPC_NAME" {
  type = string
}

variable "max_azs" {
  type = number
}

variable "vpc_cidr" {
  type = string
}

variable "public_subnets" {
  type = list(string)
}

variable "private_subnets" {
  type = list(string)
}

variable "POSTGRES_USER" {
  type = string
}

variable "POSTGRES_DB" {
  type = string
}

variable "postgres_version" {
  description = "PostgreSQL engine version for the RDS instance. Update this value to upgrade the database version. EOL schedule: 16 → Oct 2028, 17 → Nov 2029."
  type        = string
  default     = "17.9"
}

variable "flip_keypair" {
  type = string
}

variable "ec2_public_key_path" {
  type = string
}

variable "AES_KEY_BASE64" {
  type = string
}

variable "TRUST_API_KEY_HASHES" {
  description = "JSON string mapping trust names to SHA-256 hashes of their API keys"
  type        = string
}

variable "INTERNAL_SERVICE_KEY_HASH" {
  description = "SHA-256 hash of the internal service key used for fl-server-to-hub auth"
  type        = string
}

variable "FLIP_BUCKET_NAME" {
  type = string
}

variable "AICENTRE_BUCKET_NAME" {
  type = string
}

variable "FLIP_UI_BUCKET_NAME" {
  description = "S3 bucket name for flip-ui static assets served by CloudFront. Must be globally unique."
  type        = string
}

variable "flip_user_pool_name" {
  description = "Cognito User Pool name for FLIP"
  type        = string
}

variable "flip_cognito_researcher_email" {
  description = "Cognito Researcher email for FLIP"
  type        = string
}

variable "ADMIN_USER_PASSWORD" {
  description = "Default password for FLIP admin user on Cognito"
  type        = string
}

variable "flip_cognito_client" {
  description = "Cognito App Client name for FLIP"
  type        = string
}

variable "flip_cognito_admin_email" {
  description = "Cognito Admin email for FLIP"
  type        = string
}

variable "DB_PORT" {
  description = "Port for the FLIP database central hub"
  type        = number
  default     = 5432
}

variable "UI_PORT" {
  description = "Port for FLIP UI"
  type        = number
  default     = 443
}

variable "ALB_HTTPS_PORT" {
  description = "HTTPS port for ALB external access"
  type        = number
  default     = 443
}

variable "ALB_HTTP_PORT" {
  description = "HTTP port for ALB redirect to HTTPS"
  type        = number
  default     = 80
}

variable "API_PORT" {
  description = "Port for FLIP API"
  type        = number
  default     = 8080
}

variable "FL_API_PORT" {
  description = "Port for FLIP FL API"
  type        = number
  default     = 8000
}

variable "FL_SERVER_PORT" {
  description = "Port for FLIP FL Server"
  type        = number
  default     = 8002
}

variable "flip_alb_subdomain" {
  description = "Subdomain for the FLIP ALB"
  type        = string
  default     = "dev.flip.aicentre.co.uk"
}

variable "flip_nlb_subdomain" {
  description = "Subdomain for the FLIP FL server NLB endpoint"
  type        = string
  default     = "fl.dev.flip.aicentre.co.uk"
}

variable "SES_VERIFIED_EMAIL" {
  description = "SES verified email address for FLIP"
  type        = string
}

variable "XNAT_PORT" {
  description = "Port for XNAT service"
  type        = number
  default     = 8104
}

variable "PACS_UI_PORT" {
  description = "Port for Orthanc PACS UI"
  type        = number
  default     = 8042
}

variable "local_trust_public_ip" {
  description = "Public IP of an on-premises Trust host. When non-empty, an AWS security group rule is created to allow FL communication from this IP to the Central Hub NLB."
  type        = string
  default     = ""
}

variable "create_central_hub_elastic_ip" {
  description = "Whether to create an Elastic IP for the Central Hub EC2 instance. When true, ensures a persistent IP address across instance restarts and redeployments."
  type        = bool
  default     = true
}

############################
# ECS variables
############################

variable "ecs_cluster_name" {
  description = "Name of the ECS cluster"
  type        = string
  default     = "flip-ecs-cluster"
}

variable "docker_image_tag" {
  description = "Docker image tag to deploy for all FLIP services (e.g. 'latest', 'v1.2.3', or a Git SHA)"
  type        = string
  default     = "latest"
}

variable "fl_backend" {
  description = "Federated learning backend to use. One of 'flower' or 'nvflare'."
  type        = string
  default     = "flower"
}

variable "ecs_desired_count" {
  description = "Number of ECS task instances to run for each service"
  type        = number
  default     = 1
}

variable "ecs_flip_api_cpu" {
  description = "CPU units for the flip-api ECS task (1024 = 1 vCPU)"
  type        = number
  default     = 1024
}

variable "ecs_flip_api_memory" {
  description = "Memory (MiB) for the flip-api ECS task"
  type        = number
  default     = 2048
}

variable "ecs_trust_api_cpu" {
  description = "CPU units for the trust-api ECS task"
  type        = number
  default     = 2048
}

variable "ecs_trust_api_memory" {
  description = "Memory (MiB) for the trust-api ECS task"
  type        = number
  default     = 8192
}

variable "ecs_imaging_api_cpu" {
  description = "CPU units for the imaging-api ECS task"
  type        = number
  default     = 2048
}

variable "ecs_imaging_api_memory" {
  description = "Memory (MiB) for the imaging-api ECS task"
  type        = number
  default     = 8192
}

variable "ecs_data_access_api_cpu" {
  description = "CPU units for the data-access-api ECS task"
  type        = number
  default     = 2048
}

variable "ecs_data_access_api_memory" {
  description = "Memory (MiB) for the data-access-api ECS task"
  type        = number
  default     = 8192
}

############################
# Parameter Store input variables
# These populate SSM parameters consumed by ECS task definitions.
# Sensitive values use SecureString type in parameter_store.tf.
############################

variable "db_password" {
  description = "FLIP central-hub RDS master user password. Stored in FLIP_API Secrets Manager secret under key 'db_password'."
  type        = string
  sensitive   = true
}

variable "github_username" {
  description = "GitHub username for authenticating GHCR image pulls in ECS"
  type        = string
  default     = ""
}

variable "github_pat" {
  description = "GitHub Personal Access Token with read:packages scope for GHCR image pulls in ECS"
  type        = string
  sensitive   = true
  default     = ""
}

variable "PRIVATE_API_KEY_HEADER" {
  description = "HTTP header name used for the FLIP private API key (e.g. X-API-Key)"
  type        = string
  default     = "X-Api-Key"
}

variable "PRIVATE_API_KEY" {
  description = "Shared secret for service-to-service authentication between Central Hub and Trust services"
  type        = string
  sensitive   = true
}

variable "central_hub_api_url" {
  description = "Base URL of the FLIP Central Hub API reachable from Trust services (e.g. https://dev.flip.aicentre.co.uk:8080)"
  type        = string
  default     = ""
}

variable "data_access_api_url" {
  description = "Internal URL of the data-access-api service (e.g. http://data-access-api:8000)"
  type        = string
  default     = ""
}

variable "imaging_api_url" {
  description = "Internal URL of the imaging-api service (e.g. http://imaging-api:8000)"
  type        = string
  default     = ""
}

variable "xnat_url" {
  description = "XNAT server URL accessible from imaging-api"
  type        = string
  default     = ""
}

variable "xnat_service_user" {
  description = "XNAT service account username for imaging-api"
  type        = string
  default     = ""
}

variable "xnat_service_password" {
  description = "XNAT service account password for imaging-api"
  type        = string
  sensitive   = true
  default     = ""
}

variable "pacs_id" {
  description = "Orthanc PACS identifier used by imaging-api"
  type        = string
  default     = ""
}

variable "xnat_database_url" {
  description = "XNAT PostgreSQL database connection URL for imaging-api"
  type        = string
  sensitive   = true
  default     = ""
}

variable "omop_db_service_name" {
  description = "Hostname or service name of the OMOP PostgreSQL instance"
  type        = string
  default     = ""
}

variable "data_access_postgres_user" {
  description = "PostgreSQL username for data-access-api OMOP database access"
  type        = string
  default     = ""
}

variable "data_access_postgres_password" {
  description = "PostgreSQL password for data-access-api OMOP database access"
  type        = string
  sensitive   = true
  default     = ""
}

variable "omop_postgres_db" {
  description = "OMOP PostgreSQL database name"
  type        = string
  default     = ""
}
