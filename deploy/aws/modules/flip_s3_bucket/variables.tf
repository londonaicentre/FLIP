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

variable "sse_algorithm" {
  description = <<-EOT
    Server-side encryption algorithm for this bucket: "aws:kms" (the default,
    using kms_key_arn) or "AES256" (SSE-S3). AES256 exists for the one bucket
    class that a CloudFront distribution reads directly through OAC: a
    CloudFront service principal cannot decrypt with an account CMK unless the
    key policy grants it, and on a cross-account edge (LZA) not even that is
    enough for the AWS-managed aws/s3 key. aws_s3_bucket.flip_ui is on AES256
    for exactly this reason; the demo-assets bucket shares its serving path.
  EOT
  type        = string
  default     = "aws:kms"

  validation {
    condition     = contains(["aws:kms", "AES256"], var.sse_algorithm)
    error_message = "sse_algorithm must be \"aws:kms\" or \"AES256\"."
  }
}

variable "extra_bucket_policy_statements_json" {
  description = <<-EOT
    Additional IAM policy statements, as a JSON array string, merged into this
    bucket's single policy document alongside DenyHTTP (and the optional MFA
    statement). S3 allows exactly one bucket policy per bucket, so a caller
    that needs its own grant — e.g. the CloudFront OAC read on the demo-assets
    bucket — cannot add a second aws_s3_bucket_policy resource and must pass it
    here. A JSON string rather than a list(object) because the statements are
    heterogeneous (Condition/Principal shapes differ) and Terraform would have
    to unify them into one object type.
  EOT
  type        = string
  default     = "[]"
}

variable "noncurrent_version_expiration_days" {
  description = "Days after which noncurrent object versions are expired. 0 (default) creates no lifecycle configuration. Versioning is always on, so buckets whose objects are routinely deleted or replaced (e.g. model-file staging, where the scan pipeline deletes rejected uploads and moves promoted ones) otherwise retain every superseded version forever."
  type        = number
  default     = 0
}
