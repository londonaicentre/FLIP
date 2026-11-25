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

# Lifecycle policy for the ECR pull-through cache repositories (FLIP#1796,
# Security Hub ECR.3). The cache rules are created out of band (see
# iam_ecs.tf) and ECR creates a repository on the first pull of each image
# name, so this root cannot declare the repositories themselves.

locals {
  # The cache rules' repository prefixes, as iam_ecs.tf's pull-through policy names them.
  ecr_pull_through_prefixes = ["ghcr", "ecr-public"]

  # Services pin images by tag (sha-<7>, vX.Y.Z, stag), and an expired one is
  # re-imported from upstream on its next pull. No untagged rule: untagged
  # images here are platform manifests of a tagged index, or digests a running
  # ECS deployment still pulls by.
  ecr_pull_through_lifecycle_policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the newest 100 tagged images"
      selection = {
        tagStatus      = "tagged"
        tagPatternList = ["*"]
        countType      = "imageCountMoreThan"
        countNumber    = 100
      }
      action = { type = "expire" }
    }]
  })
}

# Repositories the caches create from now on are born with the policy. Mutable
# like the existing ones: the cache re-syncs moving upstream tags (stag).
resource "aws_ecr_repository_creation_template" "pull_through" {
  for_each = toset(local.ecr_pull_through_prefixes)

  prefix               = each.key
  description          = "FLIP pull-through cache ${each.key}/"
  applied_for          = ["PULL_THROUGH_CACHE"]
  image_tag_mutability = "MUTABLE"
  lifecycle_policy     = local.ecr_pull_through_lifecycle_policy

  encryption_configuration {
    encryption_type = "AES256"
  }
}

# Templates only apply to repositories created later, so the existing ones get
# the policy directly. The first pull of a new image name creates a repository
# that the next plan picks up here: an idempotent put of the same policy.
data "aws_ecr_repositories" "all" {}

resource "aws_ecr_lifecycle_policy" "pull_through" {
  for_each = toset([
    for name in data.aws_ecr_repositories.all.names : name
    if anytrue([for prefix in local.ecr_pull_through_prefixes : startswith(name, "${prefix}/")])
  ])

  repository = each.key
  policy     = local.ecr_pull_through_lifecycle_policy
}
