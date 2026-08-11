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

# In-VPC probe: run the Phase A curls before the networking side exists, and
# later verify TGW return-path routing from inside (README runbook). SSM-only
# access, mirroring the Central Hub bastion in ../main.tf: no key pair, no
# ingress rules, reachable exclusively through the LZA central ssm/
# ssmmessages/ec2messages endpoints. It doubles as the seal check — from
# here, nothing outbound should resolve to anything but the endpoints.

module "probe_role" {
  source                = "terraform-aws-modules/iam/aws//modules/iam-assumable-role"
  version               = "~> 5.0"
  role_name             = "${local.name_prefix}-probe-role"
  create_role           = "true"
  trusted_role_services = ["ec2.amazonaws.com"]
  custom_role_policy_arns = [
    "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
  ]
  role_requires_mfa = "false"
}

resource "aws_iam_instance_profile" "probe_profile" {
  name = "${local.name_prefix}-probe-profile"
  role = module.probe_role.iam_role_name
}

module "probe_security_group" {
  source        = "../modules/secgroup"
  name          = "${local.name_prefix}-probe-sg"
  vpc_id        = data.aws_vpc.lza_prod.id
  description   = "e2e LZA probe instance (no inbound - access via SSM Session Manager)"
  ingress_rules = []
}

resource "aws_ec2_tag" "probe_security_group_flip_sg" {
  resource_id = module.probe_security_group.security_group.id
  key         = "FlipSG"
  value       = "true"
}

data "aws_ssm_parameter" "ubuntu" {
  name = "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
}

resource "aws_instance" "probe" {
  tags = {
    Name = "${local.name_prefix}-probe"
  }
  # TGW-reachable only: from a -b subnet the 0.0.0.0/0 → TGW route is
  # blackholed (no attachment ENI in that AZ) and the SSM agent never
  # registers — see the tgw_reachable locals in main.tf.
  subnet_id                   = local.tgw_reachable_app_subnet_ids[0]
  associate_public_ip_address = false
  instance_type               = "t3.micro"
  ami                         = data.aws_ssm_parameter.ubuntu.value
  vpc_security_group_ids      = [module.probe_security_group.security_group.id]
  iam_instance_profile        = aws_iam_instance_profile.probe_profile.name

  root_block_device {
    volume_size           = 10
    volume_type           = "gp3"
    delete_on_termination = true
    encrypted             = true
  }
}
