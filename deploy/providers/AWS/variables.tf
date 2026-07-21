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

variable "K8S_TRUST_IP" {
  description = "DEPRECATED (kept for back-compat): single public IP of a Kubernetes-deployed trust host. Prefer k8s_trust_public_ips. When set, it is merged into the NLB ingress rule set."
  type        = string
  default     = ""
  sensitive   = true
}

variable "k8s_trust_public_ips" {
  description = "Public/egress IPs of Kubernetes-deployed trust nodes. Each IP gets an ingress rule on the FL-server NLB security group. Set via K8S_TRUST_PUBLIC_IPS in the env file (an HCL list). Managed by a normal `terraform apply` — no -target, no drift, idempotent on re-add (mirrors local_trust_public_ips)."
  type        = list(string)
  default     = []
}

variable "deploy_trust_ec2" {
  description = "Whether to provision the cloud Trust EC2 host. Set false (make full-deploy-hub-only / DEPLOY_TRUST_EC2=false) for a hub-only deployment where every trust runs on-prem — e.g. when the workloads need a GPU that the t3 trust instance doesn't have. On-prem trusts join via register-trusts + allow-local-trust-nlb as usual."
  type        = bool
  default     = true
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

variable "FL_KIT_SLOT_NAMES" {
  description = "JSON list (as a string) of FL kit-slot names that seed the fl_kit_slot pool register_trust claims from on the flip-api ECS task. Empty ⇒ NoFreeKitSlotError on trust registration. Mirrors compose.production.yml; set via the env file (TF_VAR_FL_KIT_SLOT_NAMES)."
  type        = string
  default     = "[]"
}

variable "docker_image_tag" {
  description = "Docker image tag for flip-api and flip-ui"
  type        = string
  default     = ""

  validation {
    condition     = lower(var.docker_image_tag) != "latest" && !endswith(lower(var.docker_image_tag), "-latest")
    error_message = "docker_image_tag must not be 'latest' (case-insensitive). Use an explicit immutable tag."
  }
}

variable "flip_fl_image_tag" {
  description = "Docker image tag for FL services (fl-api, fl-server, fl-client)"
  type        = string
  default     = ""

  validation {
    condition     = lower(var.flip_fl_image_tag) != "latest" && !endswith(lower(var.flip_fl_image_tag), "-latest")
    error_message = "flip_fl_image_tag must not be 'latest' (case-insensitive). Use an explicit immutable tag."
  }
}

variable "docker_registry" {
  description = "Docker image registry prefix (e.g. ghcr.io/londonaicentre/)"
  type        = string
  default     = "ghcr.io/londonaicentre/"
}

variable "fl_api_name" {
  description = "FL API Docker image name (backend-specific: flare-fl-api or flower-fl-api)"
  type        = string
  default     = "flare-fl-api"
}

variable "fl_server_name" {
  description = "FL server Docker image name (backend-specific: flare-fl-server or flower-fl-server)"
  type        = string
  default     = "flare-fl-server"
}

variable "fl_client_name" {
  description = "FL client Docker image name (backend-specific: flare-fl-client or flower-fl-client)"
  type        = string
  default     = "flare-fl-client"
}

variable "fl_backend" {
  description = "FL backend: nvflare or flower"
  type        = string
  default     = "nvflare"
}

variable "flare_kit_date" {
  description = "Date stamp for the NVFLARE provisioned kit (e.g. 20260429), used to construct the S3 path for cert syncing"
  type        = string
  default     = ""
}

variable "flower_kit_date" {
  description = "Date stamp for the Flower provisioned kit"
  type        = string
  default     = ""
}

variable "MIN_CLIENTS" {
  description = "Minimum number of FL clients required before the server starts training"
  type        = number
  default     = 1
}

# Per-job GPU resource spec requested by the fl-api when it builds an NVFLARE
# job's meta (mirrors JOB_RESOURCE_SPEC_* in compose.production.nvflare.yml).
# This drives client-side GPU allocation — the hub's Fargate tasks are CPU-only;
# the GPUs are provided by the fl-clients (trust hosts). Default 0 = no GPU
# requirement (a CPU-only tutorial). Set to 1 in the env file (TF_VAR_...) for
# GPU jobs such as the Ark+ evaluation.
variable "JOB_RESOURCE_SPEC_NUM_GPUS" {
  description = "Number of GPUs requested per FL client in a training/eval job's NVFLARE resource_spec"
  type        = number
  default     = 0
}

variable "JOB_RESOURCE_SPEC_MEM_PER_GPU_IN_GIB" {
  description = "Memory (GiB) requested per GPU in a training/eval job's NVFLARE resource_spec"
  type        = number
  default     = 0
}

# Gates the whole FL-on-ECS stack, not just the file system: both FL task
# definitions (fl-api-net-1, fl-server-net-1 in ecs_tasks.tf) carry
# `count = var.enable_efs ? 1 : 0`, so their EFS volumes (including the shared
# checkpoint volume) AND their environment (SERVER_CHECKPOINT_ROOT) exist
# together or not at all — there is no partial state where the env var is set
# but the mount is missing. Toggle together with enable_service_discovery:
# the FL services in ecs_services.tf are gated on that flag but hard-reference
# these task definitions at index [0], so enable_efs=false alone fails
# `terraform plan` with an invalid-index error (pre-existing coupling).
variable "enable_efs" {
  description = "Enable EFS file system for FL task persistent storage"
  type        = bool
  default     = true
}

variable "enable_ecs_endpoints" {
  description = "Enable VPC interface endpoints (SSM, Secrets Manager, CloudWatch Logs) for ECS Fargate"
  type        = bool
  default     = true
}

variable "enable_service_discovery" {
  description = "Enable Cloud Map Service Discovery namespace"
  type        = bool
  default     = true
}

variable "FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME" {
  description = "Globally-unique S3 bucket name for researcher-uploaded model files (browser presigned-PUT surface today; narrows to presigned POST once PR #438 lands). Required, no default — must be set per environment in the matching .env.*."
  type        = string
}

variable "FLIP_FL_RESULTS_BUCKET_NAME" {
  description = "Globally-unique S3 bucket name for FL training results (browser presigned-GET surface). Required, no default — must be set per environment in the matching .env.*."
  type        = string
}

variable "FLIP_APP_BUNDLES_BUCKET_NAME" {
  description = "Globally-unique S3 bucket name for FL app bundles (server-only; never browser-direct). Required, no default — must be set per environment in the matching .env.*."
  type        = string
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
  default     = 8104
}

variable "PACS_UI_PORT" {
  description = "Port for Orthanc PACS UI"
  type        = number
  default     = 8042
}

variable "TRUST_API_KEY_HEADER" {
  description = "HTTP header name carrying per-trust API keys on trust-to-hub calls. Compose default: Authorization."
  type        = string
  default     = "Authorization"
}

variable "INTERNAL_SERVICE_KEY_HEADER" {
  description = "HTTP header name carrying the internal service key on fl-server-to-flip-api callbacks. Required — no default so every env file must set it explicitly."
  type        = string
}

variable "FL_ADMIN_DIRECTORY" {
  description = "Container path that fl-api looks at for NVFLARE admin secrets (local + startup subdirs)."
  type        = string
  default     = "/app/admin"
}

variable "ENFORCE_MFA" {
  description = <<-EOT
    Gate authenticated routes on TOTP enrolment. Leave unset for the secure
    default — flip-api's Pydantic Settings anchors `ENFORCE_MFA = True` when
    the env var is absent from the container. Set explicitly (typically
    `false` in `.env.stag`) only to override for testing. Empty string is
    treated as unset and the variable is omitted from the ECS task env so
    the Settings default applies.
  EOT
  type        = string
  default     = ""
}

variable "local_trust_public_ips" {
  description = "Public IPs of on-premises Trust hosts. Each IP gets an ingress rule on the FL-server NLB security group allowing FL communication to the Central Hub. Set via LOCAL_TRUST_PUBLIC_IPS in the env file."
  type        = list(string)
  default     = []
}

variable "ecs_exec_enabled" {
  description = "Enable ECS Exec (execute-command) on Fargate tasks. Default false; set to true for debugging sessions via 'aws ecs execute-command'."
  type        = bool
  default     = false
}
