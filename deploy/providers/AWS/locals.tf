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

# Centralised locals consumed by ECS task definitions in PR 2. Keeping the env
# var maps here means that "what env vars does the task get?" lives in one file
# next to the compose-file source of truth — making it harder to forget vars
# like LOCAL_DEV=false or UPLOADED_FEDERATED_DATA_BUCKET when the task defs
# are introduced.

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
  # host mapping — the container port IS the reachable port. Service Discovery
  # resolves to the task IP, so fl-server calls flip-api on port 8000.
  api_container_port = 8000

  # Buckets and S3 paths used by the central hub services. Both reference
  # the resource (not var.FLIP_BUCKET_NAME) so a future bucket rename only
  # has to land in one place.
  flip_bucket_arn             = aws_s3_bucket.flip_bucket.arn
  uploaded_federated_data_uri = "s3://${aws_s3_bucket.flip_bucket.id}/uploaded_federated_data"

  # Env vars per service. Mirrors compose.production.yml +
  # compose.production.{nvflare,flower}.yml. PR 2 reads these into the ECS
  # task definitions so the deploy-time and runtime view are kept in sync.
  ecs_task_env = {
    flip_api = {
      ENV                            = "production"
      AWS_REGION                     = var.AWS_REGION
      AWS_SECRET_NAME                = "FLIP_API" # pragma: allowlist secret
      UPLOADED_FEDERATED_DATA_BUCKET = local.uploaded_federated_data_uri
    }
    fl_server = {
      LOCAL_DEV                      = "false"
      NET_ID                         = "net-1"
      MIN_CLIENTS                    = tostring(var.MIN_CLIENTS)
      IMAGES_DIR                     = "/app/data/images"
      UPLOADED_FEDERATED_DATA_BUCKET = local.uploaded_federated_data_uri
      FLIP_API_INTERNAL_URL          = "http://${local.service_discovery_names.flip_api}:${local.api_container_port}/api"
    }
    fl_api = {
      # Filled in PR 2 once backend-specific (nvflare vs flower) ports are wired.
      # Kept here as an explicit empty map so PR 2 can extend without restructuring.
    }
  }
}
