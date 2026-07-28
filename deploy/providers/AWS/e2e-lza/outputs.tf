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

output "VpcId" {
  description = "Sealed workload VPC ID"
  value       = module.vpc.vpc_id
}

output "PrivateSubnetIds" {
  description = "Private subnet IDs (the VPC's only subnets)"
  value       = module.vpc.private_subnets
}

output "AlbDnsName" {
  description = "Internal web ALB DNS name (Phase A: curl this from the probe)"
  value       = module.web_alb.dns_name
}

output "NlbDnsName" {
  description = "Internal FL NLB DNS name (Phase A: curl this from the probe on :8002)"
  value       = module.fl_nlb.dns_name
}

output "NlbPrivateIps" {
  description = "Internal FL NLB static private IPs — the networking account's edge NLB targets"
  value       = local.fl_nlb_private_ips
}

output "ProbeInstanceId" {
  description = "In-VPC probe instance ID"
  value       = aws_instance.probe.id
}

output "SsmCommand" {
  description = "SSM Session Manager command to connect to the probe"
  value       = "aws ssm start-session --target ${aws_instance.probe.id}"
}

output "EcrRepositoryUrl" {
  description = "ECR repository for the echo image — push target for the operator (see README)"
  value       = aws_ecr_repository.echo.repository_url
}

output "SsmParameterNames" {
  description = "SSM handoff parameter names published for the networking account"
  value = [
    aws_ssm_parameter.vpc_id.name,
    aws_ssm_parameter.private_subnet_ids.name,
    aws_ssm_parameter.alb_dns_name.name,
    aws_ssm_parameter.nlb_private_ips.name,
    aws_ssm_parameter.web_port.name,
    aws_ssm_parameter.fl_port.name,
  ]
}
