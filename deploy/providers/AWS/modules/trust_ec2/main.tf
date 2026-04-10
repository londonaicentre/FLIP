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

data "aws_ssm_parameter" "ubuntu" {
  name = "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
}

resource "aws_security_group" "trust_host_sg" {
  count       = length(var.security_group_ids) == 0 ? 1 : 0
  name        = "trust-host-sg-${var.name_prefix}"
  description = "Security group for trust EC2 host"
  vpc_id      = data.aws_subnet.selected.vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "ssh" {
  count             = length(var.security_group_ids) == 0 ? 1 : 0
  security_group_id = aws_security_group.trust_host_sg[0].id
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 22
  ip_protocol       = "tcp"
  to_port           = 22
}

# NOTE This is opening XNAT for the whole internet. Since these are 'open' trusts for testing, we can leave them as is.
# TODO Restrict these to the trust's IP ranges in the future.
resource "aws_vpc_security_group_ingress_rule" "xnat" {
  count             = length(var.security_group_ids) == 0 ? 1 : 0
  security_group_id = aws_security_group.trust_host_sg[0].id
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = var.XNAT_PORT
  ip_protocol       = "tcp"
  to_port           = var.XNAT_PORT
}

resource "aws_vpc_security_group_ingress_rule" "pacs_ui" {
  count             = length(var.security_group_ids) == 0 ? 1 : 0
  security_group_id = aws_security_group.trust_host_sg[0].id
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = var.PACS_UI_PORT
  ip_protocol       = "tcp"
  to_port           = var.PACS_UI_PORT
}

resource "aws_vpc_security_group_egress_rule" "allow_all" {
  count             = length(var.security_group_ids) == 0 ? 1 : 0
  security_group_id = aws_security_group.trust_host_sg[0].id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

data "aws_subnet" "selected" {
  id = var.subnet_id
}

locals {
  sg_ids = length(var.security_group_ids) == 0 ? [aws_security_group.trust_host_sg[0].id] : var.security_group_ids
}

resource "aws_instance" "trust_host" {
  ami                         = data.aws_ssm_parameter.ubuntu.value
  instance_type               = var.instance_type
  subnet_id                   = var.subnet_id
  key_name                    = var.key_name
  vpc_security_group_ids      = local.sg_ids
  associate_public_ip_address = true

  iam_instance_profile = var.iam_instance_profile_name

  root_block_device {
    volume_size           = 50
    volume_type           = "gp3"
    delete_on_termination = true
  }

  tags = {
    Name = "trust-host-${var.name_prefix}"
  }
}

resource "aws_eip" "trust_eip" {
  count    = var.create_elastic_ip ? 1 : 0
  instance = aws_instance.trust_host.id
}

output "instance_id" {
  value = aws_instance.trust_host.id
}

output "public_ip" {
  value = aws_instance.trust_host.public_ip
}
