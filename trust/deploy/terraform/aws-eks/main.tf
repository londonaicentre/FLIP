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

# A FLIP trust on Amazon EKS (FLIP#1439), the AKS root's twin: a cluster for the flip-trust chart and
# nothing else. It reads nothing from a hub; the kit, installed by make join, is the only coupling.

data "aws_availability_zones" "available" {
  state = "available"
}

# The aws-ec2 root's network: a VPC whose node subnet leaves through its own NAT gateway. EKS puts
# its control-plane interfaces in two zones, so a second private subnet joins the same route table;
# the nodes stay in the first, where the trust's EBS volumes are.
module "network" {
  count  = length(var.subnet_ids) == 0 ? 1 : 0
  source = "../aws-ec2/modules/network"
  name   = var.name
}

resource "aws_subnet" "control_plane" {
  count             = length(var.subnet_ids) == 0 ? 1 : 0
  vpc_id            = module.network[0].vpc_id
  cidr_block        = "10.60.2.0/24"
  availability_zone = data.aws_availability_zones.available.names[1]
  tags              = { Name = "${var.name}-control-plane" }
}

resource "aws_route_table_association" "control_plane" {
  count          = length(var.subnet_ids) == 0 ? 1 : 0
  subnet_id      = aws_subnet.control_plane[0].id
  route_table_id = module.network[0].private_route_table_id
}

locals {
  boundary       = var.permissions_boundary_arn == "" ? null : var.permissions_boundary_arn
  node_subnet_id = length(var.subnet_ids) > 0 ? var.subnet_ids[0] : module.network[0].subnet_id
  cluster_subnet_ids = length(var.subnet_ids) > 0 ? var.subnet_ids : [
    module.network[0].subnet_id, aws_subnet.control_plane[0].id,
  ]
}

data "aws_iam_policy_document" "eks_assume" {
  statement {
    actions = ["sts:AssumeRole", "sts:TagSession"]
    principals {
      type        = "Service"
      identifiers = ["eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "cluster" {
  name                 = "${var.name}-cluster"
  assume_role_policy   = data.aws_iam_policy_document.eks_assume.json
  permissions_boundary = local.boundary
}

resource "aws_iam_role_policy_attachment" "cluster" {
  role       = aws_iam_role.cluster.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

resource "aws_eks_cluster" "this" {
  # checkov:skip=CKV_AWS_58:Secrets are envelope-encrypted with an AWS-owned key by default; a customer key adds a standing cost a trust cluster does not need
  # checkov:skip=CKV_AWS_39:The public endpoint admits only operator_cidrs; a private-only one needs a jump host
  # checkov:skip=CKV_AWS_38:Covered by operator_cidrs, which refuses 0.0.0.0/0
  # checkov:skip=CKV_AWS_37:The API, audit and authenticator logs are kept; the controller manager's and scheduler's are chatty, a standing CloudWatch cost
  name     = var.name
  version  = var.kubernetes_version != "" ? var.kubernetes_version : null
  role_arn = aws_iam_role.cluster.arn

  # Standard support only: a version in extended support costs six times as much per hour, and
  # nothing here would say so.
  upgrade_policy {
    support_type = "STANDARD"
  }

  access_config {
    authentication_mode                         = "API"
    bootstrap_cluster_creator_admin_permissions = true
  }

  # The nodes reach the API server privately; the public endpoint admits the operator only.
  vpc_config {
    subnet_ids              = local.cluster_subnet_ids
    endpoint_private_access = true
    endpoint_public_access  = true
    public_access_cidrs     = var.operator_cidrs
  }

  enabled_cluster_log_types = ["api", "audit", "authenticator"]

  depends_on = [aws_iam_role_policy_attachment.cluster, aws_route_table_association.control_plane]
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

resource "aws_iam_role_policy_attachment" "node" {
  for_each = toset([
    "AmazonEKSWorkerNodePolicy",
    "AmazonEKS_CNI_Policy",
    "AmazonEC2ContainerRegistryReadOnly",
  ])
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/${each.value}"
}

# IMDS hop limit 1: a pod on the pod network, the FL client running researcher code among them,
# cannot reach the node role's credentials. Host-network agents (the CNI, the CSI node plugin) can.
resource "aws_launch_template" "node" {
  name_prefix = "${var.name}-node-"

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size           = var.node_disk_size
      volume_type           = "gp3"
      encrypted             = true
      delete_on_termination = true
    }
  }
}

resource "aws_eks_node_group" "this" {
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = "trust"
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = [local.node_subnet_id]
  ami_type        = "AL2023_x86_64_STANDARD"
  instance_types  = [var.instance_type]

  launch_template {
    id      = aws_launch_template.node.id
    version = aws_launch_template.node.latest_version
  }

  scaling_config {
    desired_size = var.node_count
    min_size     = var.node_count
    max_size     = var.node_count
  }

  # A rolling replacement surges one node; the quota and the trust's EBS volumes allow no more.
  update_config {
    max_unavailable = 1
  }

  depends_on = [aws_iam_role_policy_attachment.node]
}

# The chart's NetworkPolicies are its isolation: the VPC CNI enforces them once told to.
resource "aws_eks_addon" "vpc_cni" {
  cluster_name         = aws_eks_cluster.this.name
  addon_name           = "vpc-cni"
  configuration_values = jsonencode({ enableNetworkPolicy = "true" })
}

resource "aws_eks_addon" "core" {
  for_each     = toset(["kube-proxy", "coredns", "eks-pod-identity-agent"])
  cluster_name = aws_eks_cluster.this.name
  addon_name   = each.value
  depends_on   = [aws_eks_node_group.this]
}

data "aws_iam_policy_document" "pod_identity_assume" {
  statement {
    actions = ["sts:AssumeRole", "sts:TagSession"]
    principals {
      type        = "Service"
      identifiers = ["pods.eks.amazonaws.com"]
    }
  }
}

# The EBS CSI controller's own role, through Pod Identity rather than the node role.
resource "aws_iam_role" "ebs_csi" {
  name                 = "${var.name}-ebs-csi"
  assume_role_policy   = data.aws_iam_policy_document.pod_identity_assume.json
  permissions_boundary = local.boundary
}

resource "aws_iam_role_policy_attachment" "ebs_csi" {
  role       = aws_iam_role.ebs_csi.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
}

# The chart's claims name no storage class, so the driver's gp3 class is made the default.
resource "aws_eks_addon" "ebs_csi" {
  cluster_name         = aws_eks_cluster.this.name
  addon_name           = "aws-ebs-csi-driver"
  configuration_values = jsonencode({ defaultStorageClass = { enabled = true } })

  pod_identity_association {
    role_arn        = aws_iam_role.ebs_csi.arn
    service_account = "ebs-csi-controller-sa"
  }

  depends_on = [aws_eks_addon.core, aws_iam_role_policy_attachment.ebs_csi]
}

# XNAT's archive on EFS. Container Service runs each conversion as its own pod with xnat-web's
# volume mounted, on whichever node it lands, so the volume must be mountable from every node: an
# EBS volume attaches to one, and a conversion scheduled on another waits on it for ever.
resource "aws_efs_file_system" "xnat" {
  # checkov:skip=CKV_AWS_184:Encrypted with the AWS-managed key, as the node disks are; the trust's data has no separate key policy.
  # checkov:skip=CKV2_AWS_18:A cluster's archive is rebuilt from the PACS; no AWS Backup plan, as for its EBS volumes.
  creation_token = "${var.name}-xnat"
  encrypted      = true
  tags           = { Name = "${var.name}-xnat" }
}

resource "aws_security_group" "efs" {
  name        = "${var.name}-efs"
  description = "NFS to the XNAT archive from the cluster's nodes"
  vpc_id      = aws_eks_cluster.this.vpc_config[0].vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "efs_from_nodes" {
  security_group_id            = aws_security_group.efs.id
  description                  = "NFS from the cluster security group, which the nodes carry"
  ip_protocol                  = "tcp"
  from_port                    = 2049
  to_port                      = 2049
  referenced_security_group_id = aws_eks_cluster.this.vpc_config[0].cluster_security_group_id
}

resource "aws_efs_mount_target" "xnat" {
  file_system_id  = aws_efs_file_system.xnat.id
  subnet_id       = local.node_subnet_id
  security_groups = [aws_security_group.efs.id]
}

resource "aws_iam_role" "efs_csi" {
  name                 = "${var.name}-efs-csi"
  assume_role_policy   = data.aws_iam_policy_document.pod_identity_assume.json
  permissions_boundary = local.boundary
}

resource "aws_iam_role_policy_attachment" "efs_csi" {
  role       = aws_iam_role.efs_csi.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEFSCSIDriverPolicy"
}

# The add-on makes no storage class: join applies one for this file system (make storage-class).
resource "aws_eks_addon" "efs_csi" {
  cluster_name = aws_eks_cluster.this.name
  addon_name   = "aws-efs-csi-driver"

  pod_identity_association {
    role_arn        = aws_iam_role.efs_csi.arn
    service_account = "efs-csi-controller-sa"
  }

  depends_on = [aws_eks_addon.core, aws_iam_role_policy_attachment.efs_csi, aws_efs_mount_target.xnat]
}
