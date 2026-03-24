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
# Application Load Balancer
############################

module "alb_security_group" {
  source      = "./modules/secgroup"
  name        = "alb-security-group"
  vpc_id      = module.flip_vpc.vpc_id
  description = "Security group for FLIP ALB"
  ingress_rules = [
    {
      port        = var.ALB_HTTPS_PORT
      description = "HTTPS traffic"
    },
    {
      port        = var.API_PORT
      description = "API traffic"
    },
    {
      port        = var.FL_API_PORT
      description = "FL API traffic"
    },
    {
      port        = var.ALB_HTTP_PORT
      description = "HTTP traffic (redirect to HTTPS)"
    }
  ]
}

module "alb" {
  source                     = "terraform-aws-modules/alb/aws"
  name                       = "flip-alb"
  vpc_id                     = module.flip_vpc.vpc_id
  subnets                    = module.flip_vpc.public_subnets
  security_groups            = [module.alb_security_group.security_group.id]
  enable_deletion_protection = false

  listeners = {
    "https-listener" = {
      port            = var.ALB_HTTPS_PORT
      protocol        = "HTTPS"
      certificate_arn = aws_acm_certificate.flip.arn
      forward = {
        target_group_key = "ec2-instance-ui"
      }
    },
    "http-redirect" = {
      port     = var.ALB_HTTP_PORT
      protocol = "HTTP"
      redirect = {
        port        = tostring(var.ALB_HTTPS_PORT)
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    },
    "api-listener" = {
      port     = var.API_PORT
      protocol = "HTTP"
      forward = {
        target_group_key = "ec2-instance-api"
      }
    },
    "fl-api-listener" = {
      port     = var.FL_API_PORT
      protocol = "HTTP"
      forward = {
        target_group_key = "ec2-instance-fl-api"
      }
    }
  }

  target_groups = {
    ec2-instance-ui = {
      port      = var.UI_PORT
      protocol  = "HTTP"
      target_id = aws_instance.ec2_instance.id
    },
    ec2-instance-api = {
      port      = var.API_PORT
      protocol  = "HTTP"
      target_id = aws_instance.ec2_instance.id

      health_check = {
        enabled  = true
        protocol = "HTTP"
        path     = "/api/health"
        port     = "traffic-port"
        matcher  = "200"
      }
    },
    ec2-instance-fl-api = {
      port      = var.FL_API_PORT
      protocol  = "HTTP"
      target_id = aws_instance.ec2_instance.id
    }
  }
}

# Listener rule for path-based routing to the API namespace
resource "aws_lb_listener_rule" "api_routing" {
  listener_arn = module.alb.listeners["https-listener"].arn
  priority     = 98

  action {
    type             = "forward"
    target_group_arn = module.alb.target_groups["ec2-instance-api"].arn
  }

  condition {
    path_pattern {
      values = ["/api", "/api/*"]
    }
  }
}
