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

variable "bucket_name" {
  type        = string
  description = "Globally-unique S3 bucket name. No default — every caller must pass an explicit name so a typo in one tenant never collides with another."
}

variable "cors_methods" {
  type        = list(string)
  description = "HTTP methods to allow on this bucket via CORS (e.g. [\"POST\"] for browser presigned uploads, [\"GET\"] for browser downloads). Leave empty for server-only buckets — no aws_s3_bucket_cors_configuration resource is created in that case, so the bucket exposes no CORS surface to the browser."
  default     = []
}

variable "cors_allowed_origins" {
  type        = list(string)
  description = "Browser origins permitted to make CORS calls against this bucket. Typically the public canonical https://<flip_alb_subdomain>. Ignored when cors_methods is empty."
  default     = []
}
