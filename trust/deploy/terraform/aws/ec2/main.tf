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

# A FLIP trust node on EC2 (FLIP#1528). It reads nothing from a hub: no remote state, no hub env
# file, no hub credentials. The kit, delivered out of band or read from kit_bucket_name, is the only
# coupling. trust/deploy/ansible/aws-ec2.yml configures the host this root creates.

module "network" {
  source             = "../modules/network"
  name               = var.name
  existing_subnet_id = var.subnet_id
}

locals {
  boundary = var.permissions_boundary_arn == "" ? null : var.permissions_boundary_arn

  # Outbound only. The hub's API and the image registries are HTTPS, apt mirrors are HTTP, and the
  # FL endpoint is the one destination 443 does not cover. It is internet-facing, so its name
  # resolves to public addresses even from inside the hub's own VPC, and a peer security group could
  # never match it. DNS goes to the VPC resolver only; AWS does not let a security group filter it,
  # so those two rules record intent, and the absence of any other port-53 rule is what keeps
  # third-party resolvers out.
  egress = concat(
    [
      { key = "https", port = 443, protocol = "tcp", cidr = "0.0.0.0/0", description = "HTTPS: hub API, image registries, apt/esm, PyPI, AWS APIs" },
      { key = "http", port = 80, protocol = "tcp", cidr = "0.0.0.0/0", description = "HTTP: Ubuntu apt mirrors" },
      { key = "dns-udp", port = 53, protocol = "udp", cidr = "${cidrhost(module.network.vpc_cidr, 2)}/32", description = "VPC DNS resolver" },
      { key = "dns-tcp", port = 53, protocol = "tcp", cidr = "${cidrhost(module.network.vpc_cidr, 2)}/32", description = "VPC DNS resolver (TCP fallback)" },
    ],
    [for p in var.fl_server_ports : { key = "fl-${p}", port = p, protocol = "tcp", cidr = "0.0.0.0/0", description = "Hub FL endpoint (internet-facing)" }],
  )
}

resource "aws_security_group" "node" {
  name        = "${var.name}-node"
  description = "FLIP trust node: no inbound; admin access is SSM Session Manager only"
  vpc_id      = module.network.vpc_id
  tags        = { Name = "${var.name}-node" }
}

resource "aws_vpc_security_group_egress_rule" "node" {
  for_each          = { for r in local.egress : r.key => r }
  security_group_id = aws_security_group.node.id
  description       = each.value.description
  ip_protocol       = each.value.protocol
  from_port         = each.value.port
  to_port           = each.value.port
  cidr_ipv4         = each.value.cidr
}

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "node" {
  name                 = "${var.name}-node"
  assume_role_policy   = data.aws_iam_policy_document.ec2_assume.json
  permissions_boundary = local.boundary
}

# CloudWatchAgentServerPolicy reads only the agent's own AmazonCloudWatch-* parameters.
resource "aws_iam_role_policy_attachment" "cloudwatch_agent" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
}

# Session Manager and the agent's own channel, in place of AmazonSSMManagedInstanceCore: that grants
# ssm:GetParameter* and GetDocument on everything in the account, which in the hub's account would
# include the parameters register-trusts hands kits over in. Nothing on the host reads a parameter.
data "aws_iam_policy_document" "ssm_session" {
  statement {
    actions   = ["ssm:UpdateInstanceInformation"]
    resources = ["arn:aws:ec2:${var.region}:*:instance/*"]
  }

  # These take no resource-level permissions.
  statement {
    actions = [
      "ssmmessages:CreateControlChannel",
      "ssmmessages:CreateDataChannel",
      "ssmmessages:OpenControlChannel",
      "ssmmessages:OpenDataChannel",
      "ec2messages:AcknowledgeMessage",
      "ec2messages:DeleteMessage",
      "ec2messages:FailMessage",
      "ec2messages:GetEndpoint",
      "ec2messages:GetMessages",
      "ec2messages:SendReply",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "ssm_session" {
  name   = "ssm-session"
  role   = aws_iam_role.node.id
  policy = data.aws_iam_policy_document.ssm_session.json
}

# Read-only, and only this node's own kit: its FLARE slot directory, or Flower's CA certificate and
# its own SuperNode credential, at any kit date and net, plus the vocabulary. This host runs
# researcher-submitted FL code, and one role per node is what makes slot scoping expressible
# (FLIP#1164). Listing stays prefix-wide: it reveals object names only.
data "aws_iam_policy_document" "kit_read" {
  count = var.kit_bucket_name == "" ? 0 : 1

  statement {
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::${var.kit_bucket_name}"]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values = [
        "fl-flare-participant-kits/*", "fl-flare-participant-kits",
        "fl-flower-participant-kits/*", "fl-flower-participant-kits",
        "vocab/*", "vocab",
      ]
    }
  }

  statement {
    actions = ["s3:GetObject"]
    resources = [
      "arn:aws:s3:::${var.kit_bucket_name}/fl-flare-participant-kits/*/net-*/services/Trust_${var.kit_slot}/*",
      "arn:aws:s3:::${var.kit_bucket_name}/fl-flower-participant-kits/*/net-*/certificates/ca.crt",
      "arn:aws:s3:::${var.kit_bucket_name}/fl-flower-participant-kits/*/net-*/keys/supernode_credentials_${var.kit_slot}",
      "arn:aws:s3:::${var.kit_bucket_name}/vocab/*",
    ]
  }

  dynamic "statement" {
    for_each = var.kit_kms_key_arn == "" ? [] : [var.kit_kms_key_arn]
    content {
      actions   = ["kms:Decrypt"]
      resources = [statement.value]
      condition {
        test     = "StringEquals"
        variable = "kms:ViaService"
        values   = ["s3.${var.region}.amazonaws.com"]
      }
    }
  }
}

resource "aws_iam_role_policy" "kit_read" {
  count  = var.kit_bucket_name == "" ? 0 : 1
  name   = "kit-read"
  role   = aws_iam_role.node.id
  policy = data.aws_iam_policy_document.kit_read[0].json
}

resource "aws_iam_instance_profile" "node" {
  name = "${var.name}-node"
  role = aws_iam_role.node.name
}

resource "aws_key_pair" "node" {
  key_name   = var.name
  public_key = var.public_key
}

data "aws_ssm_parameter" "ami" {
  name = var.ami_ssm_parameter
}

resource "aws_instance" "node" {
  # checkov:skip=CKV_AWS_126:Detailed monitoring is not needed; the CloudWatch agent ships the host's logs and metrics
  # checkov:skip=CKV_AWS_135:Every current instance type is EBS-optimised by default
  ami                         = data.aws_ssm_parameter.ami.value
  instance_type               = var.instance_type
  subnet_id                   = module.network.subnet_id
  key_name                    = aws_key_pair.node.key_name
  vpc_security_group_ids      = [aws_security_group.node.id]
  associate_public_ip_address = false
  iam_instance_profile        = aws_iam_instance_profile.node.name

  root_block_device {
    volume_size           = var.root_volume_size
    volume_type           = "gp3"
    delete_on_termination = true
    encrypted             = true
  }

  # IMDSv2 only, hop limit 1: a container on a bridge network is one hop further and cannot reach
  # the role's credentials. The FL client runs researcher code, and nothing in a container needs
  # them: the kit and vocabulary are fetched by the host's AWS CLI, and the CloudWatch and SSM
  # agents run on the host.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  # The SSM parameter moves with every Canonical release, and a new AMI forces replacement, which
  # would take the trust's data with the root volume. -replace still launches from the current image.
  lifecycle {
    ignore_changes = [ami]
  }

  tags = { Name = "${var.name}-node" }
}
