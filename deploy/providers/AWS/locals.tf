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

  # Buckets and S3 paths used by the central hub services. Each reference goes
  # through the module output (not the variable) so a future bucket rename
  # only has to land in one place.
  #
  # After the split, FL results live in their own purpose-built bucket. The
  # ECS task env below pins fl-server / flip-api to the `${bucket}/results`
  # path, not the bucket root, as a workaround for a leading-slash bug in
  # the FL package's S3 upload path-construction
  # (flip-fl-base/flip/core/standard.py:415-427: when `urlparse(bucket).path`
  # is empty, the concatenation `f"{prefix}/{key}"` produces `/<key>` with
  # a literal leading slash, so flip-api's `list_objects_v2(Prefix=<model_id>)`
  # never matches the keys fl-server actually uploads). Removing this
  # workaround requires patching flip-fl-base, rebuilding fl-server, and
  # redeploying — see PR description on FLIP#465 for the long-term fix path.
  flip_model_files_uploads_bucket_uri = "s3://${module.flip_model_files_uploads_bucket.bucket_id}"
  flip_fl_results_bucket_uri          = "s3://${module.flip_fl_results_bucket.bucket_id}"
  flip_app_bundles_bucket_uri         = "s3://${module.flip_app_bundles_bucket.bucket_id}"

  # Sub-paths under the new three-bucket layout. The legacy single-bucket
  # prefixes (`model_files/uploaded`, `uploaded_federated_data`,
  # `base-application`, `app_destination_bucket`) are re-pointed
  # at the new purpose-built buckets per the migration mapping in
  # `make migrate-flip-bucket` (see deploy/providers/AWS/Makefile). Keeping
  # the same local names (`uploaded_federated_data_uri`, …) means the
  # ecs_task_env map below can stay byte-identical with the prior single-bucket
  # layout — only the value behind each local changes.
  # fl_app_base_uri is the backend-agnostic root: flip-api appends the per-backend
  # segment (.../base-application/{nvflare,flower}/...) in code from each net's seeded
  # (canonical) backend. A framework switch is applied via `make restart-fl`, which
  # recreates flip-api so seeding re-applies the backend onto every net.
  uploaded_federated_data_uri = "${local.flip_fl_results_bucket_uri}/results"
  uploaded_model_files_uri    = "${local.flip_model_files_uploads_bucket_uri}/uploaded"
  scanned_model_files_uri     = "${local.flip_model_files_uploads_bucket_uri}/uploaded"
  fl_app_base_uri             = "${local.flip_app_bundles_bucket_uri}/base-application"
  fl_app_destination_uri      = "${local.flip_app_bundles_bucket_uri}/app_destinations"

  # NET_ENDPOINTS tells flip-api how to reach each FL network's fl-api. On
  # ECS the hostname differs from compose (compose uses Docker DNS:
  # flip-fl-api-net-1; ECS uses Cloud Map: fl-api-net-1.flip.local), so we
  # build it here from service discovery rather than passing through .env.
  net_endpoints_json = jsonencode({
    "net-1" = "http://${local.service_discovery_names.fl_api}:${local.api_container_port}"
  })

  # ENFORCE_MFA is merged into flip_api env only when explicitly set by the
  # operator (see `var.ENFORCE_MFA` description). Unset → omitted entirely so
  # flip-api's Pydantic Settings default (`ENFORCE_MFA = True`, see
  # `flip-api/src/flip_api/config.py:91`) anchors the secure value. This
  # matches the design recorded in CLAUDE.md: "the Settings default (`true`)
  # is the canonical secure anchor". Set ENFORCE_MFA=false in `.env.stag` to
  # disable MFA for stag-only testing (per TROUBLESHOOTING.md §4.3).
  enforce_mfa_env = var.ENFORCE_MFA == "" ? {} : { ENFORCE_MFA = var.ENFORCE_MFA }

  # Env vars per service. Mirrors compose.production.yml +
  # compose.production.nvflare.yml. ECS task definitions in ecs_tasks.tf read
  # these so the deploy-time and runtime view are kept in sync.
  ecs_task_env = {
    flip_api = merge(local.enforce_mfa_env, {
      ENV                       = "production"
      AWS_REGION                = var.AWS_REGION
      AWS_COGNITO_USER_POOL_ID  = module.cognito.user_pool_id
      AWS_COGNITO_APP_CLIENT_ID = module.cognito.app_client_id
      POSTGRES_USER             = var.POSTGRES_USER
      POSTGRES_DB               = var.POSTGRES_DB
      # No DB password: flip-api authenticates to Postgres via RDS Proxy with
      # per-connection IAM tokens (DB_HOST = proxy endpoint, see ecs_tasks.tf),
      # which fixes the secret-rotation outage (FLIP#556).
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
      # Raise the per-file model-upload cap from the 100 MiB Settings default
      # to 5 GB. This is the practical ceiling for the current upload path: a
      # browser presigned POST (services.tf) is a *single* S3 POST, and S3
      # rejects any single PUT/POST over 5 GiB — larger files would need
      # multipart upload, which is not implemented. 5e9 (SI 5 GB) is used
      # rather than a binary 5 GiB so the encoded multipart/form-data body
      # (file + _MULTIPART_OVERHEAD_BUFFER_BYTES + framing) stays safely under
      # S3's 5 GiB hard limit. Note: the presigned-POST TTL is clamped to
      # 1800s (MAX_PUT_PRESIGNED_URL_TTL_SECONDS), so multi-GB uploads must
      # complete within 30 minutes or they time out at the edge.
      MAX_MODEL_FILE_BYTES = "5000000000"
    })
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
