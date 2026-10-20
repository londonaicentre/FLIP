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
# Images are mirrored into the account by digest (`make mirror-images`, #1582); nothing pulls from
# GHCR at run time.
resource "snowflake_image_repository" "images" {
  database   = snowflake_database.trust.name
  schema     = snowflake_schema.trust["app"].name
  name       = local.image_repository
  encryption = "SNOWFLAKE_SSE"
  comment    = "FLIP ${local.code}: SPCS image repository."
}
