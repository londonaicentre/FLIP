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
# Single source of truth for what the PROD flag means (FLIP#749). Sibling of
# instance.mk and fl_backend.mk, included by every Makefile that reads PROD —
# root, deploy/providers/AWS, trust, trust/xnat, fl-services/{nvflare,flower} —
# at the very top, BEFORE their env-file include: this file needs only PROD,
# and the env file's name is one of the things it derives.
#
#   PROD       ENV          .env file          compose      ENV_CLASS  IS_LZA
#   (unset)    development  .env.development   development  stag       -
#   stag       stag         .env.stag          production   stag       -
#   true       production   .env.production    production   prod       -
#   lza        lza-prod     .env.lza-prod      production   prod       yes
#   lza-stag   lza-stag     .env.lza-stag      production   stag       yes
#
# ENV is the token in every per-environment file name: the hub env file
# .env.$(ENV) and the trust kit files trust/.env.<CODE>.$(ENV) that the AWS-side
# register-trusts writes and trust/Makefile reads back. The LZA pair gets its
# own tokens so the parallel-running legacy prod/stag kits are never touched
# during the migration; everything else about it follows ENV_CLASS (prod-grade
# hardening, which git ref deploys track, which CA workspace mints FL kits) or
# IS_LZA (the platform-managed network).
#
# ENV_MODE_PROD is what is actually mapped. It defaults to PROD; the AWS
# Makefile sets it to `stag` when PROD is unset, because there an unset PROD
# has always meant "staging", not "development".
ENV_MODE_PROD ?= $(PROD)

ifeq ($(ENV_MODE_PROD),true)
ENV := production
else ifeq ($(ENV_MODE_PROD),stag)
ENV := stag
else ifeq ($(ENV_MODE_PROD),lza)
ENV := lza-prod
else ifeq ($(ENV_MODE_PROD),lza-stag)
ENV := lza-stag
else ifeq ($(strip $(ENV_MODE_PROD)),)
ENV := development
else
$(error PROD must be unset, stag, true, lza or lza-stag — got '$(ENV_MODE_PROD)')
endif

# Repo-root-relative name of the hub env file; each Makefile prefixes its own
# path to the repo root.
ENV_FILE_NAME := .env.$(ENV)
# Compose-file suffix: every deployed environment runs the production compose.
__DCKR_SUFFIX := $(if $(filter development,$(ENV)),development,production)
# The four PROD values that name a deployed environment. IS_DEPLOYED is the
# matched value (non-empty iff PROD is one of them) — usable in `ifneq` and in
# recipe-level `[ -n "$(IS_DEPLOYED)" ]` tests alike.
DEPLOYED_PROD_VALUES := true stag lza lza-stag
IS_DEPLOYED := $(filter $(DEPLOYED_PROD_VALUES),$(ENV_MODE_PROD))
# Non-empty for the two production-grade estates (legacy prod and LZA prod).
IS_PROD_GRADE := $(filter true lza,$(ENV_MODE_PROD))
# Non-empty for the two platform-managed (LZA) estates.
IS_LZA := $(filter lza lza-stag,$(ENV_MODE_PROD))
# prod | stag — the environment class Terraform, the deploy git ref and the FL
# kit CA workspace key on. Development counts as stag (the value every
# consumer already fell through to).
ENV_CLASS := $(if $(IS_PROD_GRADE),prod,stag)
