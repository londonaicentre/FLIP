# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# The hub root moved to deploy/aws (FLIP#1438). This stub forwards `make -C deploy/providers/AWS <goals>`
# there for one release, then goes. Command-line variables reach the sub-make through MAKEFLAGS, but a
# relative path in one now resolves from deploy/aws, one level shallower.
# Named GNUmakefile, not Makefile, so git records deploy/aws/Makefile as a rename.

NEW_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))../../aws)

.DEFAULT_GOAL := _forward
.PHONY: _forward $(MAKECMDGOALS)

_forward:
	@echo "⚠️  deploy/providers/AWS moved to deploy/aws (FLIP#1438); forwarding. Use 'make -C deploy/aws ...'." >&2
	@$(MAKE) --no-print-directory -C $(NEW_DIR) $(MAKECMDGOALS)

$(filter-out _forward,$(MAKECMDGOALS)): _forward
	@:
