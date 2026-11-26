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

# FLIP_DATA_ROOT (FLIP#2113): one directory per trust, set in its kit. Under it each store has a fixed
# folder (omop, orthanc, xnat, images, cohort-snapshots), so one line places all of them on one disk; it
# moves no data, and two trusts must never share one. A store's own key (OMOP_DATA_DIR, ...) still wins.
# Unset, every store keeps the default it had before. Included after the kit is read, so the kit's
# value is the one checked.
ifeq ($(strip $(FLIP_DATA_ROOT)),/)
$(error FLIP_DATA_ROOT must name a directory below /, not / itself)
endif
FLIP_DATA_ROOT := $(patsubst %/,%,$(strip $(FLIP_DATA_ROOT)))
ifneq ($(FLIP_DATA_ROOT),)
ifneq ($(words $(FLIP_DATA_ROOT)),1)
$(error FLIP_DATA_ROOT must be one path with no spaces, not '$(FLIP_DATA_ROOT)')
endif
ifeq ($(filter /%,$(FLIP_DATA_ROOT)),)
$(error FLIP_DATA_ROOT must be an absolute path, not '$(FLIP_DATA_ROOT)': compose and the sub-makes would resolve it from different directories)
endif
endif

# $(call data_root_or,<folder>,<default>): <folder> under FLIP_DATA_ROOT when the kit sets it, else <default>.
data_root_or = $(if $(FLIP_DATA_ROOT),$(FLIP_DATA_ROOT)/$(1),$(2))
