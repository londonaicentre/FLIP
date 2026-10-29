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

# The acceptance criteria of FLIP#1528 that a plan can show. The mock provider creates nothing, so
# runs use apply where a value is only known after one.

mock_provider "aws" {
  mock_data "aws_availability_zones" {
    defaults = { names = ["eu-west-2a"] }
  }
  mock_data "aws_subnet" {
    defaults = { vpc_id = "vpc-0site" }
  }
  mock_data "aws_vpc" {
    defaults = { cidr_block = "10.20.0.0/16" }
  }
  mock_data "aws_ssm_parameter" {
    defaults = { value = "ami-0123456789abcdef0" }
  }
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  mock_resource "aws_instance" {
    defaults = { arn = "arn:aws:ec2:eu-west-2:000000000000:instance/i-0mock" }
  }
}

variables {
  name       = "flip-trust-test"
  public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMockKeyForTests test"
}

run "existing_subnet_creates_no_network_and_no_address" {
  command = apply

  variables {
    subnet_id = "subnet-0site"
  }

  assert {
    condition     = length(module.network.nat_gateway_ids) == 0 && output.nat_public_ip == ""
    error_message = "On a site subnet the root creates no NAT gateway and no Elastic IP."
  }
  assert {
    condition     = aws_instance.node.subnet_id == "subnet-0site" && aws_security_group.node.vpc_id == "vpc-0site"
    error_message = "The host and its security group go in the site's subnet and VPC."
  }
  assert {
    condition     = aws_vpc_security_group_egress_rule.node["dns-udp"].cidr_ipv4 == "10.20.0.2/32"
    error_message = "DNS is allowed to the site VPC's resolver only."
  }
}

run "no_subnet_creates_its_own_network_with_a_nat_gateway" {
  command = apply

  assert {
    condition     = length(module.network.nat_gateway_ids) == 1
    error_message = "Without subnet_id the root creates its own network, leaving through one NAT gateway."
  }
}

run "host_has_no_public_ip_no_inbound_rule_and_imdsv2_only" {
  command = apply

  assert {
    condition     = aws_instance.node.associate_public_ip_address == false
    error_message = "The host must have no public IP."
  }
  assert {
    condition     = length(aws_security_group.node.ingress) == 0 && length(aws_vpc_security_group_ingress_rule.ssm_endpoints) == 0
    error_message = "The node's security group has no inbound rule; admin access is SSM only."
  }
  assert {
    condition     = aws_instance.node.metadata_options[0].http_tokens == "required"
    error_message = "IMDSv2 only."
  }
  assert {
    condition     = aws_instance.node.metadata_options[0].http_put_response_hop_limit == 1
    error_message = "Hop limit 1: containers, the FL client among them, must not reach the instance role's credentials."
  }
  assert {
    condition     = aws_instance.node.root_block_device[0].encrypted && aws_instance.node.root_block_device[0].volume_size == 100
    error_message = "The root volume is encrypted and 100 GiB by default."
  }
  assert {
    condition     = aws_instance.node.instance_type == "t3.xlarge"
    error_message = "The default instance type stays t3.xlarge."
  }
}

run "egress_allows_the_fl_endpoint_ports_given" {
  command = plan

  variables {
    fl_server_ports = [8002, 8003]
  }

  assert {
    condition     = contains(keys(aws_vpc_security_group_egress_rule.node), "fl-8002") && contains(keys(aws_vpc_security_group_egress_rule.node), "fl-8003")
    error_message = "Each FL endpoint port gets its own egress rule."
  }
  assert {
    condition     = length(aws_vpc_security_group_egress_rule.node) == 6
    error_message = "Egress is HTTPS, HTTP, DNS (udp+tcp) and the FL ports, nothing else."
  }
}

run "ssm_access_is_sessions_only_never_parameters" {
  command = plan

  assert {
    condition     = aws_iam_role_policy_attachment.cloudwatch_agent.policy_arn == "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
    error_message = "The only managed policy is the CloudWatch agent's."
  }
  assert {
    condition     = length([for a in flatten(data.aws_iam_policy_document.ssm_session.statement[*].actions) : a if startswith(a, "ssm:") && a != "ssm:UpdateInstanceInformation"]) == 0
    error_message = "The node may not read SSM parameters or documents: in the hub's account those hold kits."
  }
}

run "no_kit_bucket_grants_no_s3_or_kms" {
  command = plan

  assert {
    condition     = length(aws_iam_role_policy.kit_read) == 0
    error_message = "Without kit_bucket_name the node gets no S3 access: the kit is delivered out of band."
  }
}

run "kit_bucket_grants_read_on_this_slots_kit_only" {
  command = plan

  variables {
    kit_bucket_name = "hub-kits"
    kit_slot        = 2
    kit_kms_key_arn = "arn:aws:kms:eu-west-2:000000000000:key/mock"
  }

  assert {
    condition     = length(aws_iam_role_policy.kit_read) == 1
    error_message = "kit_bucket_name grants the node a read policy."
  }
  assert {
    condition = toset(data.aws_iam_policy_document.kit_read[0].statement[1].resources) == toset([
      "arn:aws:s3:::hub-kits/fl-flare-participant-kits/*/net-*/services/Trust_2/*",
      "arn:aws:s3:::hub-kits/fl-flower-participant-kits/*/net-*/certificates/ca.crt",
      "arn:aws:s3:::hub-kits/fl-flower-participant-kits/*/net-*/keys/supernode_credentials_2",
      "arn:aws:s3:::hub-kits/vocab/*",
    ])
    error_message = "GetObject covers this slot's kit and the vocabulary, never another trust's kit or the server's."
  }
}

run "kit_bucket_without_a_slot_is_refused" {
  command = plan

  variables {
    kit_bucket_name = "hub-kits"
  }

  expect_failures = [var.kit_slot]
}

run "permissions_boundary_applies_to_every_role" {
  command = plan

  variables {
    permissions_boundary_arn = "arn:aws:iam::000000000000:policy/lza-boundary"
    schedule_enabled         = true
  }

  assert {
    condition     = aws_iam_role.node.permissions_boundary == var.permissions_boundary_arn && aws_iam_role.scheduler[0].permissions_boundary == var.permissions_boundary_arn
    error_message = "Every role this root creates carries the boundary."
  }
}

run "schedule_is_off_by_default" {
  command = plan

  assert {
    condition     = length(aws_scheduler_schedule.node) == 0 && length(aws_iam_role.scheduler) == 0
    error_message = "The stop/start schedule is opt-in."
  }
}

run "schedule_on_starts_and_stops_the_node" {
  command = plan

  variables {
    schedule_enabled = true
  }

  assert {
    condition     = toset(keys(aws_scheduler_schedule.node)) == toset(["start", "stop"])
    error_message = "schedule_enabled creates a start and a stop schedule."
  }
  assert {
    condition     = aws_scheduler_schedule.node["stop"].schedule_expression_timezone == "Europe/London"
    error_message = "The schedule runs in the given time zone."
  }
}

run "ssm_endpoints_are_opt_in" {
  command = plan

  variables {
    ssm_endpoints = true
  }

  assert {
    condition     = toset(keys(aws_vpc_endpoint.ssm)) == toset(["ssm", "ssmmessages", "ec2messages"])
    error_message = "ssm_endpoints creates the three SSM interface endpoints."
  }
}

run "name_must_be_a_valid_resource_name" {
  command = plan

  variables {
    name = "Not_Valid"
  }

  expect_failures = [var.name]
}
