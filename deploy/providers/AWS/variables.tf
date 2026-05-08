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

variable "environment" {
  description = "Deployment environment. 'prod' enables RDS hardening (deletion protection + final snapshot); any other value (e.g. 'stag') keeps the database disposable for fast tear-down."
  type        = string
  default     = "stag"
  validation {
    condition     = contains(["prod", "stag"], var.environment)
    error_message = "environment must be either 'prod' or 'stag'."
  }
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

variable "INTERNAL_SERVICE_KEY" {
  description = "Raw internal service key used by fl-server to authenticate callbacks to flip-api. Stored in Secrets Manager (FLIP_API secret) and consumed by the fl-server ECS task definition via the secrets block."
  type        = string
  sensitive   = true
}

variable "docker_image_tag" {
  description = "Container image tag for ECS task definitions. Empty default — deploys must pass an explicit tag (Git SHA preferred). 'latest' is intentionally not the default to avoid the mutable-tag pitfalls flagged in the v1 review."
  type        = string
  default     = ""
}

variable "MIN_CLIENTS" {
  description = "Minimum number of FL clients required before the server starts training"
  type        = number
  default     = 1
}

variable "enable_efs" {
  description = "Enable EFS file system for FL task persistent storage. Gated to false until PR 2 adds ECS task definitions that mount EFS volumes — creating EFS without consumers is dead infra."
  type        = bool
  default     = false
}

variable "enable_ecs_endpoints" {
  description = "Enable VPC interface endpoints (SSM, Secrets, Logs, ECR) for ECS Fargate. Gated to false until PR 2 adds ECS services that consume them — existing EC2 traffic uses the NAT gateway. Unconditionally creating endpoints before PR 2 incurs ~$73/month idle cost."
  type        = bool
  default     = false
}

variable "enable_service_discovery" {
  description = "Enable Cloud Map Service Discovery namespace. Gated to false until PR 2 adds ECS services that register — creating the namespace without registrants is dead infra."
  type        = bool
  default     = false
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
  description = "Public canonical subdomain for FLIP. Aliased via Route53 to the CloudFront distribution; CloudFront fronts both the SPA (from S3) and the API (/api/* -> ALB). Name is retained for Terraform-state backwards compatibility - see main.tf:492-494."
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
}

variable "PACS_UI_PORT" {
  description = "Port for Orthanc PACS UI"
  type        = number
}

variable "local_trust_public_ip" {
  description = "Public IP of an on-premises Trust host. When non-empty, AWS security group rules are created to allow consolidated FL communication on port 8002 from this IP to the Central Hub."
  type        = string
  default     = ""
}
