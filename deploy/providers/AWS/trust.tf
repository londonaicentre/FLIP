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
# Trust EC2
############################

module "trust_ec2" {
  source = "./modules/trust_ec2"

  name_prefix   = "trust"
  instance_type = "t3.xlarge"
  key_name      = aws_key_pair.host_key.key_name
  subnet_id     = element(module.flip_vpc.public_subnets, 0)

  # use the trust SG, not the central EC2 SG
  security_group_ids = [module.trust_security_group.security_group.id]

  TRUST_API_PORT = var.TRUST_API_PORT
  XNAT_PORT      = var.XNAT_PORT
  PACS_UI_PORT   = var.PACS_UI_PORT

  # pass the compose file content and env file content from the repo
  create_elastic_ip = true
  # attaches the same ec2-role-profile instance profile to the Trust instance
  iam_instance_profile_name = aws_iam_instance_profile.ec2_profile.name
}

resource "aws_key_pair" "host_key" {
  key_name   = "host-aws"
  public_key = file(var.ec2_public_key_path)
}

############################
# On-Premises Trust (optional)
# Activated by setting local_trust_public_ip in the env file or via
# TF_VAR_local_trust_public_ip when running `make add-local-trust`.
############################

resource "aws_security_group_rule" "local_trust_fl_server" {
  count             = var.local_trust_public_ip != "" ? 1 : 0
  type              = "ingress"
  from_port         = 8002
  to_port           = 8002
  protocol          = "tcp"
  cidr_blocks       = ["${var.local_trust_public_ip}/32"]
  security_group_id = module.ec2_security_group.security_group.id
  description       = "FL Server from on-prem Trust"
}

resource "aws_security_group_rule" "local_trust_fl_admin" {
  count             = var.local_trust_public_ip != "" ? 1 : 0
  type              = "ingress"
  from_port         = 8003
  to_port           = 8003
  protocol          = "tcp"
  cidr_blocks       = ["${var.local_trust_public_ip}/32"]
  security_group_id = module.ec2_security_group.security_group.id
  description       = "FL Admin from on-prem Trust"
}

# Allow the local (on-prem) trust FL client to reach the FL server via the NLB.
# Without this rule the NLB security group drops the connection before it reaches the EC2.
resource "aws_security_group_rule" "local_trust_fl_server_nlb" {
  count             = var.local_trust_public_ip != "" ? 1 : 0
  type              = "ingress"
  from_port         = var.FL_SERVER_PORT
  to_port           = var.FL_SERVER_PORT
  protocol          = "tcp"
  cidr_blocks       = ["${var.local_trust_public_ip}/32"]
  security_group_id = module.fl_server_nlb.security_group_id
  description       = "FL Server NLB from on-prem Trust"
}
