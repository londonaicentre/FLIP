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

# FLIP_DATA_ROOT (FLIP#2113): the one directory holding everything this trust stores, set in its kit.
# Under it each store has a fixed folder (omop, orthanc, xnat, images, cohort-snapshots), so a site
# moves all of them to another disk with one line. A store's own key (OMOP_DATA_DIR, ...) still wins.
# Unset, every store keeps the default it had before. Included by trust/Makefile and trust/xnat/Makefile
# after the kit file, so the kit's value is the one read.
FLIP_DATA_ROOT := $(patsubst %/,%,$(strip $(FLIP_DATA_ROOT)))
ifneq ($(FLIP_DATA_ROOT),)
ifeq ($(filter /%,$(FLIP_DATA_ROOT)),)
$(error FLIP_DATA_ROOT must be an absolute path, not '$(FLIP_DATA_ROOT)': compose and the sub-makes would resolve it from different directories)
endif
endif

# $(call data_root_or,<folder>,<default>): <folder> under FLIP_DATA_ROOT when the kit sets it, else <default>.
data_root_or = $(if $(FLIP_DATA_ROOT),$(FLIP_DATA_ROOT)/$(1),$(2))
