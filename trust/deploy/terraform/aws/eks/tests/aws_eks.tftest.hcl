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

# What a plan can show of FLIP#1439. The mock provider creates nothing, so runs apply where a value
# is only known after one.

mock_provider "aws" {
  mock_data "aws_availability_zones" {
    defaults = { names = ["eu-west-2a", "eu-west-2b"] }
  }
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::000000000000:role/mock" }
  }
  mock_resource "aws_launch_template" {
    defaults = { id = "lt-0mock", latest_version = 1 }
  }
}

variables {
  name           = "flip-trust-test"
  operator_cidrs = ["203.0.113.7/32"]
}

run "own_network_spans_two_zones_and_keeps_nodes_in_one" {
  command = apply

  assert {
    condition     = length(aws_eks_cluster.this.vpc_config[0].subnet_ids) == 2
    error_message = "EKS needs control-plane subnets in two zones."
  }
  assert {
    condition     = aws_subnet.control_plane[0].availability_zone == "eu-west-2b"
    error_message = "The control-plane subnet is in the second zone; the node subnet is in the first."
  }
  assert {
    condition     = one(aws_eks_node_group.this.subnet_ids) == module.network[0].subnet_id
    error_message = "Nodes stay in one zone, where the trust's ReadWriteOnce EBS volumes are."
  }
}

run "site_subnets_create_no_network" {
  command = plan

  variables {
    subnet_ids = ["subnet-0a", "subnet-0b"]
  }

  assert {
    condition     = length(module.network) == 0 && length(aws_subnet.control_plane) == 0
    error_message = "With site subnets the root creates no VPC, NAT gateway or subnet."
  }
  assert {
    condition     = one(aws_eks_node_group.this.subnet_ids) == "subnet-0a"
    error_message = "The nodes go in the first site subnet."
  }
}

run "one_site_subnet_is_refused" {
  command = plan

  variables {
    subnet_ids = ["subnet-0a"]
  }

  expect_failures = [var.subnet_ids]
}

run "api_endpoint_admits_the_operator_only" {
  command = plan

  assert {
    condition = (
      aws_eks_cluster.this.vpc_config[0].endpoint_private_access
      && one(aws_eks_cluster.this.vpc_config[0].public_access_cidrs) == "203.0.113.7/32"
    )
    error_message = "The nodes use the private endpoint; the public one admits operator_cidrs only."
  }
}

run "an_open_api_endpoint_is_refused" {
  command = plan

  variables {
    operator_cidrs = ["0.0.0.0/0"]
  }

  expect_failures = [var.operator_cidrs]
}

run "pods_cannot_reach_the_node_role" {
  command = plan

  assert {
    condition = (
      aws_launch_template.node.metadata_options[0].http_put_response_hop_limit == 1
      && aws_launch_template.node.metadata_options[0].http_tokens == "required"
    )
    error_message = "IMDSv2 with hop limit 1 keeps pod-network pods, the FL client among them, off the node role."
  }
}

run "network_policies_are_enforced" {
  command = plan

  assert {
    condition     = jsondecode(aws_eks_addon.vpc_cni.configuration_values).enableNetworkPolicy == "true"
    error_message = "The chart's NetworkPolicies are its isolation; the VPC CNI must enforce them."
  }
}

run "ebs_csi_uses_its_own_role_and_a_default_class" {
  command = plan

  assert {
    condition     = jsondecode(aws_eks_addon.ebs_csi.configuration_values).defaultStorageClass.enabled
    error_message = "The chart's claims name no class, so the driver's gp3 class must be the default."
  }
  assert {
    condition     = one(aws_eks_addon.ebs_csi.pod_identity_association).service_account == "ebs-csi-controller-sa"
    error_message = "The CSI controller's EC2 permissions come through Pod Identity, never the node role."
  }
}

run "xnat_archive_is_on_efs_reachable_from_the_nodes_only" {
  command = plan

  variables {
    subnet_ids = ["subnet-0aaa", "subnet-0bbb"]
  }

  assert {
    condition     = aws_efs_file_system.xnat.encrypted
    error_message = "The archive holds patient images: encrypted at rest."
  }
  assert {
    condition     = aws_efs_mount_target.xnat.subnet_id == "subnet-0aaa"
    error_message = "One mount target, in the nodes' subnet."
  }
  assert {
    condition = (
      aws_vpc_security_group_ingress_rule.efs_from_nodes.from_port == 2049 &&
      aws_vpc_security_group_ingress_rule.efs_from_nodes.to_port == 2049 &&
      aws_vpc_security_group_ingress_rule.efs_from_nodes.cidr_ipv4 == null
    )
    error_message = "NFS only, from the cluster security group rather than an address range."
  }
  assert {
    condition     = one(aws_eks_addon.efs_csi.pod_identity_association).service_account == "efs-csi-controller-sa"
    error_message = "The EFS controller's permissions come through Pod Identity, never the node role."
  }
}

run "standard_support_only" {
  command = plan

  assert {
    condition     = aws_eks_cluster.this.upgrade_policy[0].support_type == "STANDARD"
    error_message = "Extended support costs six times as much per hour."
  }
}

run "permissions_boundary_applies_to_every_role" {
  command = plan

  variables {
    permissions_boundary_arn = "arn:aws:iam::000000000000:policy/boundary"
  }

  assert {
    condition = alltrue([
      for r in [aws_iam_role.cluster, aws_iam_role.node, aws_iam_role.ebs_csi, aws_iam_role.efs_csi] : r.permissions_boundary == var.permissions_boundary_arn
    ])
    error_message = "Every role carries the boundary."
  }
}
