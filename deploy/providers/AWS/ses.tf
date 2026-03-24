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
# SES Email Templates
############################

resource "aws_ses_email_identity" "flip_sender" {
  email = var.SES_VERIFIED_EMAIL
}

resource "aws_ses_template" "flip_access_request" {
  name    = "flip-access-request"
  subject = "Access Request from {{name}} on FLIP"
  html    = file("${path.module}/templates/ses/flip-access-request.html")
  text    = file("${path.module}/templates/ses/flip-access-request.txt")
}

resource "aws_ses_template" "flip_xnat_credentials" {
  name    = "flip-xnat-credentials"
  subject = "Your XNAT credentials for {{trust_name}}"
  html    = file("${path.module}/templates/ses/flip-xnat-credentials.html")
  text    = file("${path.module}/templates/ses/flip-xnat-credentials.txt")
}
