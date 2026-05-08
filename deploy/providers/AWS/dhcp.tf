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

# Custom DHCP options: add flip.local as a search domain so EC2 instances and
# Fargate tasks can resolve bare hostnames (flip-api → flip-api.flip.local)
# without needing the FQDN. The Cloud Map private DNS namespace handles the
# actual DNS records; this is a convenience for service-to-service calls.
#
# AmazonProvidedDNS is the default. Using it explicitly here prevents
# Terraform from replacing it with nothing on re-apply.

resource "aws_vpc_dhcp_options" "flip" {
  domain_name         = local.flip_local_domain
  domain_name_servers = ["AmazonProvidedDNS"]

  tags = {
    Name = "flip-dhcp-options"
  }
}

resource "aws_vpc_dhcp_options_association" "flip" {
  vpc_id          = module.flip_vpc.vpc_id
  dhcp_options_id = aws_vpc_dhcp_options.flip.id
}
