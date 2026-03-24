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
# Security Groups
############################

# Central Hub Security Group for EC2 instance

module "ec2_security_group" {
  source      = "./modules/secgroup"
  name        = "ec2-security-group"
  vpc_id      = module.flip_vpc.vpc_id
  description = "Security group for FLIP Central Hub EC2 instance"
  ingress_rules = [
    {
      port        = var.UI_PORT
      description = "FLIP UI"
    },
    {
      port        = var.API_PORT
      description = "FLIP API"
    },
    {
      port        = var.FL_API_PORT
      description = "FLIP FL API"
    },
    {
      port        = 22
      description = "SSH access"
    }
  ]
}

# Trust Security Group for Trust EC2 instance

module "trust_security_group" {
  source      = "./modules/secgroup"
  name        = "trust-security-group"
  vpc_id      = module.flip_vpc.vpc_id
  description = "Security group for FLIP Trust EC2 instance"

  ingress_rules = [
    {
      port        = var.TRUST_API_PORT
      description = "Trust API"
    },
    {
      port        = var.XNAT_PORT
      description = "XNAT access"
    },
    {
      port        = var.PACS_UI_PORT
      description = "Orthanc PACS UI access"
    },
    {
      port        = 22
      description = "SSH access"
    }
  ]
}

# Only allow FL server traffic that arrives through the NLB, not direct client or VPC access.
resource "aws_security_group_rule" "fl_server_ingress_from_nlb" {
  type                     = "ingress"
  from_port                = var.FL_SERVER_PORT
  to_port                  = var.FL_SERVER_PORT
  protocol                 = "tcp"
  source_security_group_id = module.fl_server_nlb.security_group_id
  security_group_id        = module.ec2_security_group.security_group.id
  description              = "FL Server from NLB security group"
}

# RDS
# TODO: In Production we need to activate delete protection to the RDS instances
module "rds_security_group" {
  source      = "./modules/secgroup"
  name        = "rds-security-group"
  vpc_id      = module.flip_vpc.vpc_id
  description = "Security group for FLIP RDS instance"
  ingress_rules = [
    {
      port                     = 5432
      description              = "PostgreSQL from EC2"
      source_security_group_id = module.ec2_security_group.security_group.id
    }
  ]
  block_all_outbound = true
}
