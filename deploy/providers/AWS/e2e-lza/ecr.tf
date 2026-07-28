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

# Private ECR repository for the dummy echo image. The sealed account cannot
# reach GHCR or Docker Hub (no NAT/IGW), so the operator pushes
# hashicorp/http-echo here from their workstation — exact commands in
# README.md. Do NOT assume the LZA ECR pull-through cache exists in this
# account yet (FLIP#749 is the prod plan, not necessarily provisioned here —
# README open question 2).

resource "aws_ecr_repository" "echo" {
  name = "e2e-lza-echo"

  # Test stack: the image is a disposable public echo server, so let
  # `terraform destroy` remove the repo even while images are present.
  force_delete = true
}
