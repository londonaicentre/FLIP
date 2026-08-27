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
  # (flip-utils/flip/core/standard.py:415-427: when `urlparse(bucket).path`
  # is empty, the concatenation `f"{prefix}/{key}"` produces `/<key>` with
  # a literal leading slash, so flip-api's `list_objects_v2(Prefix=<model_id>)`
  # never matches the keys fl-server actually uploads). Removing this
  # workaround requires patching flip-utils/flip, rebuilding fl-server, and
  # redeploying — see PR description on FLIP#465 for the long-term fix path.
  flip_model_files_uploads_bucket_uri = "s3://${module.flip_model_files_uploads_bucket.bucket_id}"
  flip_fl_results_bucket_uri          = "s3://${module.flip_fl_results_bucket.bucket_id}"
  flip_app_bundles_bucket_uri         = "s3://${module.flip_app_bundles_bucket.bucket_id}"

  # Sub-paths under the new three-bucket layout. The legacy single-bucket
  # prefixes (`model_files/uploaded`, `uploaded_federated_data`,
  # `app_destination_bucket`) are re-pointed at the new purpose-built buckets
  # per the migration mapping in `make migrate-flip-bucket` (see
  # deploy/providers/AWS/Makefile). Keeping the same local names
  # (`uploaded_federated_data_uri`, …) means the ecs_task_env map below can stay
  # byte-identical with the prior single-bucket layout — only the value behind
  # each local changes.
  # The base FL application templates are no longer published to S3: they are baked
  # into the flip-api image from the repo's fl-apps/ tree and read locally via
  # FL_APP_BASE_DIR (FLIP#724). Only the completed bundle still lands in S3
  # (fl_app_destination_uri).
  # The two model-file prefixes are the quarantine boundary (#52): researcher
  # uploads land in `uploaded/` and only reach `scanned/` once flip-api's scan
  # promotes them. Everything that consumes model files — the FL app bundler,
  # downloads, listings — reads `scanned/` exclusively, so an unscanned or
  # rejected file can never be shipped to a trust. They must stay distinct.
  uploaded_federated_data_uri = "${local.flip_fl_results_bucket_uri}/results"
  uploaded_model_files_uri    = "${local.flip_model_files_uploads_bucket_uri}/uploaded"
  scanned_model_files_uri     = "${local.flip_model_files_uploads_bucket_uri}/scanned"
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

  # Env vars per service — the CANONICAL definition of production container
  # config (#936; the compose.production*.yml files mirror a subset as the
  # local prod-image harness). fl_server/fl_api are the NVFLARE maps
  # (compose.production.nvflare.yml); fl_server_flower/fl_api_flower are the
  # Flower maps (compose.production.flower.yml). ecs_tasks.tf selects by
  # var.fl_backend (#566).
  ecs_task_env = {
    flip_api = merge(local.enforce_mfa_env, {
      ENV        = "production"
      AWS_REGION = var.AWS_REGION
      # Pin boto3 to the regional S3 endpoint: the legacy global endpoint's
      # ~24h DNS lag on freshly created non-us-east-1 buckets caused the
      # FLIP#24 500s. Same rationale as the matching block in
      # deploy/compose.production.yml; was compose-only until #566.
      AWS_ENDPOINT_URL_S3       = "https://s3.${var.AWS_REGION}.amazonaws.com"
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
      # FL_KIT_SLOT_NAMES is deliberately NOT in the task env: in production the
      # kit-slot pool's single source is the /flip/fl_kit_slot_names SSM parameter
      # (parameter_store.tf), read at runtime by resolve_fl_kit_slot_names — an env
      # copy here would be dead config that apply-fl-kit-slots never updates.
    })
    fl_server = {
      LOCAL_DEV                      = "false"
      NET_ID                         = "net-1"
      MIN_CLIENTS                    = tostring(var.MIN_CLIENTS)
      IMAGES_DIR                     = "/app/data/images"
      UPLOADED_FEDERATED_DATA_BUCKET = local.uploaded_federated_data_uri
      FLIP_API_INTERNAL_URL          = "http://${local.service_discovery_names.flip_api}:${local.api_container_port}/api"
      INTERNAL_SERVICE_KEY_HEADER    = var.INTERNAL_SERVICE_KEY_HEADER
      # Root of the shared checkpoint-staging volume (mounted from the
      # fl_checkpoints EFS access point in ecs_tasks.tf). The fl-server's
      # EvaluationModelLocator reads a staged checkpoint from
      # <root>/<model_id>/ here (FLIP#695). Matches the default in
      # flip-utils FlipConstants + compose.production.nvflare.yml.
      SERVER_CHECKPOINT_ROOT = "/app/server-checkpoints"
      # INTERNAL_SERVICE_KEY is injected via the `secrets` block in
      # ecs_tasks.tf (sourced from the FLIP_API Secrets Manager secret),
      # never exposed as plain env in the task definition.
    }
    fl_api = {
      # ENV gates the entrypoint's uvicorn --reload off in prod/stag so a failed
      # startup is a dead container (replaced by ECS), not a zombie (FLIP#593 pt.1).
      ENV                = "production"
      FL_ADMIN_DIRECTORY = var.FL_ADMIN_DIRECTORY
      # Writer side of the shared checkpoint-staging volume: fl-api de-bundles a
      # large eval checkpoint out of the client app and writes it to
      # <root>/<model_id>/ for the fl-server to load (FLIP#695). Same path the
      # fl-server reads. Mounted from the fl_checkpoints EFS access point.
      SERVER_CHECKPOINT_ROOT = "/app/server-checkpoints"
      # Per-job GPU resource spec the fl-api stamps onto an NVFLARE job's meta.
      # Requests GPUs on the fl-CLIENTS (trust hosts) — the hub Fargate tasks are
      # CPU-only. Default 0; set via TF_VAR_JOB_RESOURCE_SPEC_* for GPU jobs.
      JOB_RESOURCE_SPEC_NUM_GPUS           = tostring(var.JOB_RESOURCE_SPEC_NUM_GPUS)
      JOB_RESOURCE_SPEC_MEM_PER_GPU_IN_GIB = tostring(var.JOB_RESOURCE_SPEC_MEM_PER_GPU_IN_GIB)
    }
    # Flower SuperLink (compose.production.flower.yml fl-server-net-1). TLS +
    # SuperNode-auth flags travel as the container command (ecs_tasks.tf), not
    # env. INTERNAL_SERVICE_KEY is injected via the secrets block.
    fl_server_flower = {
      LOCAL_DEV                      = "false"
      NET_ID                         = "net-1"
      MIN_CLIENTS                    = tostring(var.MIN_CLIENTS)
      IMAGES_DIR                     = "/app/data/images"
      UPLOADED_FEDERATED_DATA_BUCKET = local.uploaded_federated_data_uri
      FLIP_API_INTERNAL_URL          = "http://${local.service_discovery_names.flip_api}:${local.api_container_port}/api"
      INTERNAL_SERVICE_KEY_HEADER    = var.INTERNAL_SERVICE_KEY_HEADER
    }
    # Flower fl-api (compose.production.flower.yml fl-api-net-1). SuperLink
    # addresses use the Cloud Map name — the provisioned server cert must
    # carry it as a SAN (FLOWER_EXTRA_SERVER_SANS at provision time).
    fl_api_flower = {
      # Same reload-gating rationale as the NVFLARE map (FLIP#593 pt.1).
      ENV                         = "production"
      SUPERLINK_ADDRESS           = "${local.service_discovery_names.fl_server}:9093"
      SUPERLINK_HEALTH_ADDRESS    = "${local.service_discovery_names.fl_server}:9097"
      SUPERLINK_ROOT_CERTIFICATES = "/certs/ca.crt"
      FLOWER_SRC_ROOT             = "/app/src"
    }
  }
}
