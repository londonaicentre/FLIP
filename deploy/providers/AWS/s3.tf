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

############################
# S3
############################

resource "aws_s3_bucket" "flip_bucket" {
  bucket = var.FLIP_BUCKET_NAME
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_cors_configuration" "flip_bucket_cors" {
  bucket = aws_s3_bucket.flip_bucket.id

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["PUT"]
    allowed_origins = ["*"]
    expose_headers  = []
  }
}

# Model files uploaded by researchers will be combined with the base app here
resource "aws_s3_object" "app_destination_bucket" {
  bucket  = aws_s3_bucket.flip_bucket.id
  key     = "app_destination_bucket/"
  content = ""
  lifecycle {
    prevent_destroy = true
  }
}

# Model files uploaded by researchers will be stored here
resource "aws_s3_object" "model_files" {
  bucket  = aws_s3_bucket.flip_bucket.id
  key     = "model_files/"
  content = ""
  lifecycle {
    prevent_destroy = true
  }
}

# FL results will be stored here
resource "aws_s3_object" "uploaded_federated_data" {
  bucket  = aws_s3_bucket.flip_bucket.id
  key     = "uploaded_federated_data/"
  content = ""
  lifecycle {
    prevent_destroy = true
  }
}

############################
# AI Centre S3 Bucket
############################

resource "aws_s3_bucket" "aicentre_bucket" {
  bucket = var.AICENTRE_BUCKET_NAME
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_cors_configuration" "aicentre_bucket_cors" {
  bucket = aws_s3_bucket.aicentre_bucket.id

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["PUT", "GET"]
    allowed_origins = ["*"]
    expose_headers  = []
  }
}
