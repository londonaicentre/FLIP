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

# AWS Cloud Map private DNS namespace. ECS services in PR 2 register here, so
# tasks resolve each other by name — flip-api.flip.local, fl-api-net-1.flip.local,
# fl-server-net-1.flip.local — without depending on Docker network DNS.
#
# Cloud Map creates and manages the underlying Route53 private hosted zone
# automatically when the namespace type is DNS_PRIVATE; we do not create a
# separate aws_route53_zone for flip.local to avoid managing the same zone
# from two resources.

resource "aws_service_discovery_private_dns_namespace" "flip_local" {
  count = var.enable_service_discovery ? 1 : 0

  name        = local.flip_local_domain
  description = "Private DNS for ECS service-to-service resolution"
  vpc         = module.flip_vpc.vpc_id
}
