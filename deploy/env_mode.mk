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
# Single source of truth for what the HUB selector means (FLIP#1479). Sibling of
# instance.mk and fl_backend.mk, included by every Makefile that reads HUB —
# root, deploy/providers/AWS, trust, trust/xnat, trust/deploy/helm,
# fl-services/{nvflare,flower} — at the very top, BEFORE their env-file include:
# this file needs only HUB, and the env file's name is one of the things it derives.
#
#   HUB        ENV          .env file          compose      ENV_CLASS
#   (unset)    development  .env.development   development  stag
#   stag       stag         .env.stag          production   stag
#   prod       prod         .env.prod          production   prod
#
# ENV is the token in every per-environment file name: the hub env file
# .env.$(ENV) and the trust kit files trust/.env.<CODE>.$(ENV). Whether the estate
# runs on a self-contained or a platform-managed (LZA) network is not encoded here:
# it is a property of the estate, set by LZA_MANAGED_NETWORK in its env file.

# PROD was the selector until FLIP#1479. Refused rather than ignored, so an old
# command line cannot silently run against development.
_HUB_FOR_PROD_true := use HUB=prod
_HUB_FOR_PROD_lza := use HUB=prod
_HUB_FOR_PROD_lza-stag := use HUB=stag
_HUB_FOR_PROD_stag := PROD=stag selected the retired self-contained staging estate and is no longer supported (FLIP\#1377)
ifneq ($(strip $(PROD)),)
$(error PROD is replaced by HUB=stag|prod (FLIP#1479): $(or $(_HUB_FOR_PROD_$(strip $(PROD))),unset PROD and pass HUB) — got PROD='$(PROD)')
endif

ifeq ($(HUB),prod)
ENV := prod
else ifeq ($(HUB),stag)
ENV := stag
else ifeq ($(strip $(HUB)),)
ENV := development
else
$(error HUB must be unset, stag or prod — got '$(HUB)')
endif

# Repo-root-relative name of the hub env file; each Makefile prefixes its own
# path to the repo root.
ENV_FILE_NAME := .env.$(ENV)
# Compose-file suffix: every deployed environment runs the production compose.
__DCKR_SUFFIX := $(if $(filter development,$(ENV)),development,production)
# Non-empty iff a hub is selected — usable in `ifneq` and in recipe-level
# `[ -n "$(IS_DEPLOYED)" ]` tests alike.
IS_DEPLOYED := $(strip $(HUB))
# prod | stag — the environment class Terraform, the deploy git ref and the FL
# kit CA workspace key on. Development counts as stag.
ENV_CLASS := $(if $(filter prod,$(ENV)),prod,stag)

# Kit files. A trust's kit is trust/.env.<CODE>.$(ENV). The names used before
# FLIP#1479 — .production / .lza-prod for prod, .lza-stag for stag, and a bare
# .env.<CODE> — are still found for one release so a live site's next upgrade
# does not break; the Makefiles that find one print old_kit_hint.
OLD_KIT_ENVS := $(if $(filter prod,$(ENV)),production lza-prod,$(if $(filter stag,$(ENV)),lza-stag))
# $(call kit_file,<trust dir with trailing / or empty>,<CODE>): the first of those
# names that exists, else the current one.
kit_file = $(or $(firstword $(foreach f,$(1).env.$(2).$(ENV) $(foreach e,$(OLD_KIT_ENVS),$(1).env.$(2).$(e)) $(1).env.$(2),$(wildcard $(f)))),$(1).env.$(2).$(ENV))
# $(call kit_codes,<trust dir>): the CODE of every kit present for this hub, old names included.
kit_codes = $(sort $(foreach e,$(ENV) $(OLD_KIT_ENVS),$(patsubst $(1).env.%.$(e),%,$(wildcard $(1).env.*.$(e)))))
# $(call kit_files,<trust dir>): every such kit file, as kit_file resolves it.
kit_files = $(foreach c,$(call kit_codes,$(1)),$(call kit_file,$(1),$(c)))
# $(call old_kit_hint,<kit file>): one line asking to rename a kit found under an old name.
old_kit_hint = $(if $(filter-out %.$(ENV),$(1)),$(info ⚠️  $(1) is an old kit name — rename it to $(or $(firstword $(foreach e,$(OLD_KIT_ENVS),$(patsubst %.$(e),%.$(ENV),$(filter %.$(e),$(1))))),$(1).$(ENV))))
