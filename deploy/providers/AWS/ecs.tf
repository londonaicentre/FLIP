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
# ECS Cluster
############################

resource "aws_ecs_cluster" "flip" {
  name = var.ecs_cluster_name

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = {
    Name = var.ecs_cluster_name
  }
}

resource "aws_ecs_cluster_capacity_providers" "flip" {
  cluster_name       = aws_ecs_cluster.flip.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    base              = 1
    weight            = 100
    capacity_provider = "FARGATE"
  }
}

############################
# CloudWatch Log Groups
############################

resource "aws_cloudwatch_log_group" "ecs_flip_api" {
  name              = "/ecs/flip-api"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "ecs_trust_api" {
  name              = "/ecs/trust-api"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "ecs_imaging_api" {
  name              = "/ecs/imaging-api"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "ecs_data_access_api" {
  name              = "/ecs/data-access-api"
  retention_in_days = 7
}
