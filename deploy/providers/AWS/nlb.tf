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
# Network Load Balancer
############################

# Network Load Balancer for FL server TCP/TLS pass-through
module "fl_server_nlb" {
  source                     = "terraform-aws-modules/alb/aws"
  name                       = "flip-fl-server-nlb"
  load_balancer_type         = "network"
  vpc_id                     = module.flip_vpc.vpc_id
  subnets                    = module.flip_vpc.public_subnets
  enable_deletion_protection = false
  create_security_group      = true

  # NLB only accepts trusted client sources - allow-list only the trusted client egress IPs
  # TODO explore 'internal' NLB plus private connectivity instead of an internet-facing NLB
  security_group_ingress_rules = {
    fl_server_ingress = {
      description = "Allow inbound FL server traffic only from trusted FL client IP"
      ip_protocol = "tcp"
      from_port   = tostring(var.FL_SERVER_PORT)
      to_port     = tostring(var.FL_SERVER_PORT)
      cidr_ipv4   = "${module.trust_ec2.public_ip}/32"
    }
  }

  security_group_egress_rules = {
    fl_server_egress = {
      description = "Allow NLB traffic and health checks to FL server targets"
      ip_protocol = "tcp"
      from_port   = tostring(var.FL_SERVER_PORT)
      to_port     = tostring(var.FL_SERVER_PORT)
      cidr_ipv4   = var.vpc_cidr
    }
  }

  listeners = {
    "fl-server-tcp-listener" = {
      port     = var.FL_SERVER_PORT
      protocol = "TCP"
      forward = {
        target_group_key = "ec2-instance-fl-server-tcp"
      }
    }
  }

  target_groups = {
    ec2-instance-fl-server-tcp = {
      port        = var.FL_SERVER_PORT
      protocol    = "TCP"
      target_type = "instance"
      target_id   = aws_instance.ec2_instance.id

      health_check = {
        enabled             = true
        protocol            = "TCP"
        port                = "traffic-port"
        healthy_threshold   = 3
        unhealthy_threshold = 3
        interval            = 30
      }
    }
  }
}
