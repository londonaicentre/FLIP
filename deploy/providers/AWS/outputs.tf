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
# Central Hub
############################

output "keypair" {
  value = var.flip_keypair
}

output "ec2_instance_id" {
  description = "EC2 Instance ID"
  value       = aws_instance.ec2_instance.id
}

output "ec2_public_ip" {
  description = "EC2 Instance Public IP"
  value       = aws_instance.ec2_instance.public_ip
}

output "ssh_command" {
  description = "SSH command to connect to the instance"
  value       = "ssh -i ${var.flip_keypair} ubuntu@${aws_instance.ec2_instance.public_ip}"
}

############################
# Trust
############################

output "trust_ec2_instance_id" {
  description = "Trust EC2 Instance ID"
  value       = module.trust_ec2.instance_id
}

output "trust_ec2_public_ip" {
  description = "Trust EC2 Instance Public IP"
  value       = module.trust_ec2.public_ip
}

output "trust_ssh_command" {
  description = "SSH command to connect to the Trust EC2 instance"
  value       = "ssh -i ${var.flip_keypair} ubuntu@${module.trust_ec2.public_ip}"
}

############################
# Database
############################

output "db_endpoint" {
  description = "RDS Database Endpoint"
  value       = module.flip_db.db_instance_address
}

output "db_secret_arn" {
  description = "RDS Database Secret ARN"
  value       = module.flip_db.db_instance_master_user_secret_arn
}

############################
# Cognito
############################

output "cognito_user_pool_id" {
  description = "Cognito User Pool ID"
  value       = aws_cognito_user_pool.flip_user_pool.id
}

output "cognito_app_client_id" {
  description = "Cognito App Client ID"
  value       = aws_cognito_user_pool_client.client.id
}

############################
# FL Server
############################

output "fl_server_endpoint" {
  description = "FL server DNS endpoint (NLB pass-through)"
  value       = var.flip_nlb_subdomain
}

output "fl_server_raw_nlb_dns" {
  description = "Raw AWS NLB DNS name for FL server debugging"
  value       = module.fl_server_nlb.dns_name
}
