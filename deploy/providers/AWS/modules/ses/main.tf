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

# FLIP SES sender identity + transactional email templates.

resource "aws_ses_email_identity" "flip_sender" {
  email = var.sender_email
}

resource "aws_ses_template" "flip_access_request" {
  name    = var.template_name_prefix == "" ? "flip-access-request" : "${var.template_name_prefix}-flip-access-request"
  subject = "Access Request from {{name}} on FLIP"
  html    = file("${var.templates_dir}/flip-access-request.html")
  text    = file("${var.templates_dir}/flip-access-request.txt")
}

# Invite email: carries a host-less "set your own password" link (an XNAT alias-token path),
# never a password (FLIP-PT-079). Renamed from the retired flip-xnat-credentials template.
resource "aws_ses_template" "flip_xnat_invite" {
  name    = var.template_name_prefix == "" ? "flip-xnat-invite" : "${var.template_name_prefix}-flip-xnat-invite"
  subject = "Set your XNAT password for {{trust_name}}"
  html    = file("${var.templates_dir}/flip-xnat-invite.html")
  text    = file("${var.templates_dir}/flip-xnat-invite.txt")
}

resource "aws_ses_template" "flip_xnat_added_to_project" {
  name    = var.template_name_prefix == "" ? "flip-xnat-added-to-project" : "${var.template_name_prefix}-flip-xnat-added-to-project"
  subject = "You have been added to a project at {{trust_name}}"
  html    = file("${var.templates_dir}/flip-xnat-added-to-project.html")
  text    = file("${var.templates_dir}/flip-xnat-added-to-project.txt")
}
