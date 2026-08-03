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

variable "logging_target_bucket" {
  description = "S3 bucket name for server access logging. Set to empty string to disable."
  type        = string
  default     = ""
}

variable "mfa_delete_protection" {
  description = "Enable MFA-gated DeleteObjectVersion via bucket policy"
  type        = bool
  default     = false
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

variable "kms_key_arn" {
  description = "ARN of KMS CMK for SSE-KMS on this bucket. null (default) uses the AWS-managed key (aws/s3). When provided, the SSE block sets kms_master_key_id so S3 uses the specified CMK."
  type        = string
  default     = null
}

variable "noncurrent_version_expiration_days" {
  description = "Days after which noncurrent object versions are expired. 0 (default) creates no lifecycle configuration. Versioning is always on, so buckets whose objects are routinely deleted or replaced (e.g. model-file staging, where the scan pipeline deletes rejected uploads and moves promoted ones) otherwise retain every superseded version forever."
  type        = number
  default     = 0
}
