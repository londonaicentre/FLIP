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

# Partial configuration, exactly as in the other two roots: the type is fixed here and every
# setting arrives through `-backend-config` (the Makefile passes BACKEND_CONFIG). Each root keeps
# its OWN state file, so an apply of one can never touch what another created.
terraform {
  backend "local" {}
}
