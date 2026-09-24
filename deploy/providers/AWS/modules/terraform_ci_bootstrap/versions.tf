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

# No provider block and no backend: the caller supplies both. The platform
# repositories pass a provider aliased into the FLIP account; ../../ci configures
# its own. >= 6.0 because data.aws_region's `region` attribute arrives there; < 7.0
# because a major version is a deliberate bump, not something a platform init
# should pick up unannounced. Spans aicentre-iac's lock (6.39) and FLIP's (6.66).
terraform {
  required_version = ">= 1.13.1"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.0, < 7.0"
    }
  }
}
