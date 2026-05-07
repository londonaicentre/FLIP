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

# Centralised locals consumed by ECS task definitions. Keeping the env-var maps
# here means "what env vars does the task get?" lives in one file next to the
# compose-file source of truth - making it harder to forget vars like
# LOCAL_DEV=false or UPLOADED_FEDERATED_DATA_BUCKET when the task defs change.
#
# These maps mirror compose.production.yml + compose.production.nvflare.yml so
# the running container sees the same env whether it runs on EC2+compose or
# ECS Fargate.

locals {
  flip_local_domain = "flip.local"

  # Service Discovery names (FQDN under the private hosted zone).
  service_discovery_names = {
    flip_api  = "flip-api.${local.flip_local_domain}"
    fl_api    = "fl-api-net-1.${local.flip_local_domain}"
    fl_server = "fl-server-net-1.${local.flip_local_domain}"
  }

  # Container port for flip-api. On EC2 + Docker Compose, var.API_PORT is the
  # host-side port (8080) mapped to container port 8000. ECS Fargate has no
  # host mapping - the container port IS the reachable port. Service Discovery
  # resolves to the task IP, so fl-server calls flip-api on port 8000.
  api_container_port = 8000

  # Buckets and S3 paths used by the central hub services. References the
  # resource (not var.FLIP_BUCKET_NAME) so a future bucket rename only has to
  # land in one place. Paths mirror .env.stag values for the same env-var name.
  flip_bucket_id              = aws_s3_bucket.flip_bucket.id
  flip_bucket_arn             = aws_s3_bucket.flip_bucket.arn
  uploaded_federated_data_uri = "s3://${local.flip_bucket_id}/uploaded_federated_data"
  uploaded_model_files_uri    = "s3://${local.flip_bucket_id}/model_files/uploaded"
  scanned_model_files_uri     = "s3://${local.flip_bucket_id}/model_files/uploaded"
  fl_app_base_uri             = "s3://${local.flip_bucket_id}/base-application/${var.fl_backend}"
  fl_app_destination_uri      = "s3://${local.flip_bucket_id}/app_destination_bucket"

  # NET_ENDPOINTS tells flip-api how to reach each FL network's fl-api. On
  # ECS the hostname differs from compose (compose uses Docker DNS:
  # flip-fl-api-net-1; ECS uses Cloud Map: fl-api-net-1.flip.local), so we
  # build it here from service discovery rather than passing through .env.
  net_endpoints_json = jsonencode({
    "net-1" = "http://${local.service_discovery_names.fl_api}:${local.api_container_port}"
  })

  # Env vars per service. Mirrors compose.production.yml +
  # compose.production.nvflare.yml. ECS task definitions in ecs_tasks.tf read
  # these so the deploy-time and runtime view are kept in sync.
  ecs_task_env = {
    flip_api = {
      ENV                            = "production"
      ENFORCE_MFA                    = var.ENFORCE_MFA
      AWS_REGION                     = var.AWS_REGION
      AWS_COGNITO_USER_POOL_ID       = module.cognito.user_pool_id
      AWS_COGNITO_APP_CLIENT_ID      = module.cognito.app_client_id
      POSTGRES_USER                  = var.POSTGRES_USER
      POSTGRES_DB                    = var.POSTGRES_DB
      POSTGRES_SECRET_ARN            = module.flip_db.db_instance_master_user_secret_arn
      TRUST_API_KEY_HEADER           = var.TRUST_API_KEY_HEADER
      INTERNAL_SERVICE_KEY_HEADER    = var.INTERNAL_SERVICE_KEY_HEADER
      AWS_SECRET_NAME                = "FLIP_API" # pragma: allowlist secret
      AWS_SES_ADMIN_EMAIL_ADDRESS    = var.SES_VERIFIED_EMAIL
      AWS_SES_SENDER_EMAIL_ADDRESS   = var.SES_VERIFIED_EMAIL
      UPLOADED_MODEL_FILES_BUCKET    = local.uploaded_model_files_uri
      SCANNED_MODEL_FILES_BUCKET     = local.scanned_model_files_uri
      UPLOADED_FEDERATED_DATA_BUCKET = local.uploaded_federated_data_uri
      FL_APP_BASE_BUCKET             = local.fl_app_base_uri
      FL_APP_DESTINATION_BUCKET      = local.fl_app_destination_uri
      NET_ENDPOINTS                  = local.net_endpoints_json
      FL_BACKEND                     = var.fl_backend
      TRUST_NAMES                    = var.TRUST_NAMES
    }
    fl_server = {
      LOCAL_DEV                      = "false"
      NET_ID                         = "net-1"
      MIN_CLIENTS                    = tostring(var.MIN_CLIENTS)
      IMAGES_DIR                     = "/app/data/images"
      UPLOADED_FEDERATED_DATA_BUCKET = local.uploaded_federated_data_uri
      FLIP_API_INTERNAL_URL          = "http://${local.service_discovery_names.flip_api}:${local.api_container_port}/api"
      INTERNAL_SERVICE_KEY_HEADER    = var.INTERNAL_SERVICE_KEY_HEADER
      # INTERNAL_SERVICE_KEY is injected via the `secrets` block in
      # ecs_tasks.tf (sourced from the FLIP_API Secrets Manager secret),
      # never exposed as plain env in the task definition.
    }
    fl_api = {
      FL_ADMIN_DIRECTORY = var.FL_ADMIN_DIRECTORY
    }
  }
}
