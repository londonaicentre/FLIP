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

variable "flip_keypair" {
  type = string
}

variable "ec2_public_key_path" {
  type = string
}

variable "AES_KEY_BASE64" {
  type = string
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

variable "TRUST_API_PORT" {
  description = "Port for Trust API"
  type        = number
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
  description = "Public IP of an on-premises Trust host. When non-empty, AWS security group rules are created to allow FL communication on ports 8002 and 8003 from this IP to the Central Hub."
  type        = string
  default     = ""
}

variable "create_central_hub_elastic_ip" {
  description = "Whether to create an Elastic IP for the Central Hub EC2 instance. When true, ensures a persistent IP address across instance restarts and redeployments."
  type        = bool
  default     = true
}

############################
# VPN Configuration Variables
############################

variable "on_prem_cidr" {
  description = "CIDR block for on-premises network (e.g., xx.xx.xxx.xxx/xx). Required for VPN route configuration."
  type        = string
  default     = ""
}

variable "on_prem_public_ip" {
  description = "Static public IP address of the on-premises VPN gateway/firewall device. Must be provided by on-premises IT team. Leave empty to skip VPN creation."
  type        = string
  default     = ""
}

variable "on_prem_bgp_asn" {
  description = "BGP Autonomous System Number for on-premises customer gateway. Must be unique within the network."
  type        = number
  default     = 65000
}

variable "enable_vpn" {
  description = "Enable creation of VPN connection to on-premises network. Set to false for cloud-only deployments (no on-premises trusts)."
  type        = bool
  default     = false
}

############################
# Networking Account (Centralized TGW & VPN)
############################

variable "networking_account_id" {
  description = "AWS Account ID of the Networking Account (owns centralized TGW and VPN connection)"
  type        = string
  default     = "222257058857"
}

variable "networking_account_tgw_id" {
  description = "Transit Gateway ID from Networking Account (e.g., tgw-xxxxxxxxxx). Request from network team."
  type        = string
  default     = ""
}

variable "networking_account_tgw_route_table_id" {
  description = "TGW Route Table ID from Networking Account (for route propagation). Request from network team."
  type        = string
  default     = ""
}

variable "enable_tgw_route_propagation" {
  description = "Enable Prod/Staging VPC CIDR propagation to Networking Account TGW route table. Set to true to auto-advertise VPC routes."
  type        = bool
  default     = true
}
