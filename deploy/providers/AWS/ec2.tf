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
# EC2
############################

# IAM Role for EC2 instance
module "ec2_role" {
  source                = "terraform-aws-modules/iam/aws//modules/iam-assumable-role"
  version               = "~> 5.0"
  role_name             = "ec2-role"
  create_role           = "true"
  trusted_role_services = ["ec2.amazonaws.com"]
  custom_role_policy_arns = [
    "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
    "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy",
    "arn:aws:iam::aws:policy/AmazonCognitoPowerUser", # TODO Restrict this policy to only what we need in production
    "arn:aws:iam::aws:policy/AmazonS3FullAccess",     # TODO Restrict this policy to only what we need in production
    "arn:aws:iam::aws:policy/AmazonSESFullAccess",    # TODO Restrict this policy to only what we need in production
    "arn:aws:iam::aws:policy/SecretsManagerReadWrite" # TODO could create a read-only policy instead
  ]
  role_requires_mfa = "false"
}

resource "aws_iam_instance_profile" "ec2_profile" {
  name = "ec2-role-profile"
  role = module.ec2_role.iam_role_name
}

# Add permissions to access secrets
resource "aws_iam_role_policy" "ec2_secret" {
  name = "secret-read"
  role = module.ec2_role.iam_role_name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["secretsmanager:GetSecretValue"]
      Resource = [
        module.flip_db.db_instance_master_user_secret_arn,
        module.flip_api_secret.secret_arn
      ]
    }]
  })
}

# CloudWatch Log Group
resource "aws_cloudwatch_log_group" "flip_log_group" {
  name              = "/aws/ec2/flip"
  retention_in_days = 7
}

# Key Pair for SSH access
resource "aws_key_pair" "flip_keypair" {
  key_name   = "flip-keypair"
  public_key = file("${var.flip_keypair}.pub")
}

# EC2 Instance
data "aws_ssm_parameter" "ubuntu" {
  name = "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
}

resource "aws_instance" "ec2_instance" {
  tags = {
    Name = "Ec2Instance"
  }
  subnet_id                   = module.flip_vpc.public_subnets[0]
  associate_public_ip_address = true
  instance_type               = "t3.medium"
  ami                         = data.aws_ssm_parameter.ubuntu.value
  vpc_security_group_ids      = [module.ec2_security_group.security_group.id]
  iam_instance_profile        = aws_iam_instance_profile.ec2_profile.name
  key_name                    = aws_key_pair.flip_keypair.key_name
  root_block_device {
    volume_size           = 30
    volume_type           = "gp3"
    delete_on_termination = true
  }
}
