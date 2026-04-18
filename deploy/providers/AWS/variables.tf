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

variable "enable_ssh_key_pair" {
  description = "When true, create aws_key_pair resources and attach them to EC2 instances. Only needed for the legacy SSH-over-SSM path (scp, docker-over-SSH, git-over-SSH). All primary workflows (interactive shell, Ansible, make deploy-*, make logs) now go through SSM with IAM credentials — no key pair required."
  type        = bool
  default     = false
}

variable "flip_keypair" {
  description = "Path to local private key (without .pub suffix). Required when enable_ssh_key_pair = true."
  type        = string
  default     = ""
}

variable "ec2_public_key_path" {
  description = "Path to local public key used for the trust EC2 key pair. Required when enable_ssh_key_pair = true."
  type        = string
  default     = ""
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
}

variable "PACS_UI_PORT" {
  description = "Port for Orthanc PACS UI"
  type        = number
}

variable "local_trust_public_ip" {
  description = "Public IP of an on-premises Trust host. When non-empty, an AWS security group rule is created to allow FL communication from this IP to the Central Hub NLB."
  type        = string
  default     = ""
}
