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
# AWS EC2 trust node (FLIP#1528). Site targets need the trust's kit file only (KIT=<CODE> HUB=<env>)
# and AWS credentials for the account the node goes in; they never read a hub env file, hub state
# or hub credentials. No target skips Terraform's confirmation: apply and destroy show the plan and ask.
#
# Order: register the trust on the hub (make register-trusts KIT=<CODE> HUB=<env>), then
#   init plan apply ssh-config provision deploy      first install
#   upgrade TAG=vX.Y.Z                               move a live node to a release, data in place

SHELL := /bin/bash
REPO_ROOT := $(abspath $(CURDIR)/../../../../..)
# HUB -> ENV and the kit-file names; reads no hub env file.
include $(REPO_ROOT)/deploy/env_mode.mk

TF := terraform
ANSIBLE_DIR := $(REPO_ROOT)/trust/deploy/ansible
# Ansible from the AWS tree's locked uv project, which the on-prem play uses too, until FLIP#1438
# PR 3 gives the trust side its own.
ANSIBLE := uv run --project $(REPO_ROOT)/deploy/aws

KIT ?=
# Checked when parsed: every target derives NAME and the kit file from KIT. A KIT holding spaces is
# usually one argument carrying several assignments (zsh does not split `make init $V`).
ifneq ($(KIT),)
ifneq ($(shell [[ '$(KIT)' =~ ^[A-Za-z0-9_]+$$ ]] && echo ok),ok)
$(error KIT='$(KIT)' is not a kit code: it must match [A-Za-z0-9_]+. Pass each variable as its own argument)
endif
endif
KIT_FILE = $(call kit_file,$(REPO_ROOT)/trust/,$(KIT))
kit_get = $(shell sed -n 's/^$(1)=//p' $(KIT_FILE) 2>/dev/null | tail -1)
KIT_SLOT = $(call kit_get,FL_KIT_SLOT_NUMBER)
FL_PORT = $(call kit_get,FL_EDGE_PORT)
FL_BACKEND = $(call kit_get,FL_BACKEND)
lower = $(shell printf '%s' '$(1)' | tr '[:upper:]_' '[:lower:]-')

NAME ?= $(if $(KIT),flip-$(ENV)-$(call lower,$(KIT)))
REGION ?= eu-west-2
STATE_BUCKET ?=
STATE_KEY ?= trust-nodes/$(NAME).tfstate
SUBNET_ID ?=
PRIVATE_IP ?=
INSTANCE_TYPE ?= t3.xlarge
PUBLIC_KEY_FILE ?= $(HOME)/.ssh/host-aws.pub
SSH_KEY ?= $(HOME)/.ssh/host-aws
BOUNDARY_ARN ?=
KIT_BUCKET ?=
KIT_KMS_KEY_ARN ?=
# The hub's kit date for the trust's backend, as its kit file carries it in the Hub-shared block.
FL_KIT_DATE ?= $(call kit_get,$(if $(filter flower,$(FL_BACKEND)),FLOWER_KIT_DATE,FLARE_KIT_DATE))
SSM_ENDPOINTS ?= false
SCHEDULE ?= false
TRUST_DATA_VERSION := $(shell cat $(REPO_ROOT)/trust/.data_version 2>/dev/null)
# Exported only when set: an empty one makes the AWS CLI fail with "config profile () could not be
# found" for credentials exported as AWS_ACCESS_KEY_ID & co.
ifneq ($(AWS_PROFILE),)
export AWS_PROFILE
endif

# The plan's inputs. Destroy needs no real key: the provider parses one, so a discarded public key.
TF_VARS = -var 'name=$(NAME)' -var 'region=$(REGION)' -var 'subnet_id=$(SUBNET_ID)' -var 'private_ip=$(PRIVATE_IP)' \
	-var 'instance_type=$(INSTANCE_TYPE)' -var 'permissions_boundary_arn=$(BOUNDARY_ARN)' \
	-var 'kit_bucket_name=$(KIT_BUCKET)' $(if $(KIT_BUCKET),-var 'kit_slot=$(KIT_SLOT)') \
	-var 'kit_kms_key_arn=$(KIT_KMS_KEY_ARN)' -var 'fl_server_ports=[$(FL_PORT)]' \
	-var 'ssm_endpoints=$(SSM_ENDPOINTS)' -var 'schedule_enabled=$(SCHEDULE)'
DESTROY_PUBLIC_KEY := ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINNJS9sQraFNfnq0au6+vksilL+ct4B2O0yRqzcYY+oe destroy-only

define require_kit
	@[ -n "$(KIT)" ] || { echo "❌ KIT=<CODE> is required (the trust's kit file, trust/.env.<CODE>.<env>)."; exit 1; }
	@[ -f "$(KIT_FILE)" ] || { echo "❌ $(KIT_FILE) not found."; exit 1; }
	@[[ "$(KIT_SLOT)" =~ ^[0-9]+$$ && "$(FL_PORT)" =~ ^[0-9]+$$ ]] || { \
		echo "❌ $(notdir $(KIT_FILE)) has no FL_KIT_SLOT_NUMBER / FL_EDGE_PORT yet: register the trust first (make register-trusts KIT=$(KIT) HUB=<env>)."; exit 1; }
endef

# The bucket the node was applied with, read from this root's state so the host verbs are not given
# it again. KIT_BUCKET= on the command line, empty for none, wins. The read replaces itself with its
# result: it runs once, and only in a recipe that names it, so help, test, lint and plan never run
# terraform for it. `!` is a state that could not be read, or one applied before it held the output.
STATE_KIT_BUCKET = $(eval STATE_KIT_BUCKET := $$(shell $(TF) output -raw kit_bucket_name 2>/dev/null || echo '!'))$(STATE_KIT_BUCKET)
NODE_KIT_BUCKET = $(if $(filter file,$(origin KIT_BUCKET)),$(STATE_KIT_BUCKET),$(KIT_BUCKET))
define require_kit_bucket
	@[ "$(NODE_KIT_BUCKET)" != "!" ] || { \
		echo "❌ This root's state does not say whether the node reads a kit bucket. Run make init; for a node applied"; \
		echo "   before the kit_bucket_name output existed, plan and apply once more with the plan's own arguments."; \
		echo "   Or name it: KIT_BUCKET=<bucket>, or KIT_BUCKET= for a node with none."; exit 1; }
endef

# deploy and upgrade evaluate compose here and drive the node over the ssh docker context, so a
# relative host path in the kit resolves to this checkout and the node's daemon creates it
# root-owned: Orthanc then cannot open its database, and XNAT and the image dirs land in /Users/….
NODE_DIR_KEYS := ORTHANC_STORAGE_DIR BASE_IMAGES_DOWNLOAD_DIR XNAT_DATA_DIR OMOP_DATA_DIR COHORT_SNAPSHOT_STORAGE_DIR FL_KIT_DIR
# The play, the seed and stage-kit never read the kit's paths: they prepare, fill and stage the trees
# below (the roles' defaults and aws-ec2-host.yml's), so compose pointed anywhere else starts on
# directories that are empty, or that the node's daemon created root-owned. Unset, the first three
# take these same paths as their compose default; FL_KIT_DIR has none and must be set. XNAT_DATA_DIR
# and COHORT_SNAPSHOT_STORAGE_DIR are prepared at bring-up from the kit's own value.
# $(call node_dir_moved,<KEY>,<path>): KEY=<path> when the kit sets KEY to anything else.
node_dir_moved = $(if $(filter-out $(2) $(2)/,$(call kit_get,$(1))),$(1)=$(2))
NODE_DIRS_MOVED = $(call node_dir_moved,OMOP_DATA_DIR,/opt/flip/omop/db_data) \
	$(call node_dir_moved,ORTHANC_STORAGE_DIR,/opt/flip/orthanc/orthanc-storage) \
	$(call node_dir_moved,BASE_IMAGES_DOWNLOAD_DIR,/opt/flip/data/trust-$(KIT_SLOT)) \
	$(call node_dir_moved,FL_KIT_DIR,/opt/flip/fl-kit)
define require_node_dirs
	@bad="$(strip $(foreach k,$(NODE_DIR_KEYS),$(if $(filter-out /%,$(call kit_get,$(k))),$(k))))"; [ -z "$$bad" ] || { \
		echo "❌ $(notdir $(KIT_FILE)) sets relative host paths for a remote node: $$bad."; \
		echo "   Comment them out (each but FL_KIT_DIR defaults under /opt/flip) or make them absolute."; exit 1; }
	@bad="$(strip $(NODE_DIRS_MOVED))"; [ -z "$$bad" ] || { \
		echo "❌ $(notdir $(KIT_FILE)) names host paths this node is not provisioned with. It prepares, seeds and stages:"; \
		for d in $$bad; do echo "     $$d"; done; \
		echo "   Set them to those values (or comment them out, FL_KIT_DIR excepted): compose would otherwise start on empty directories."; exit 1; }
	@[ -n "$(call kit_get,FL_KIT_DIR)" ] || { \
		echo "❌ $(notdir $(KIT_FILE)) has no FL_KIT_DIR, and compose has no default for it: the FL client would mount /net-1."; \
		echo "   Set FL_KIT_DIR=/opt/flip/fl-kit, where stage-kit puts the kit on this node."; exit 1; }
endef

# $(call confirm_target,<question>): name the account, profile and region, then stop unless the operator types yes.
define confirm_target
	@echo "Target account: $$(aws sts get-caller-identity --query Account --output text) ($(or $(AWS_PROFILE),default profile)), region $(REGION)"
	@read -r -p "$(1)? Type yes to continue: " answer; [ "$$answer" = "yes" ] || { echo "Stopped."; exit 1; }
endef

# $(call play,<extra ansible-playbook args>): aws-ec2.yml against this node.
define play
	cd $(ANSIBLE_DIR) && $(ANSIBLE) ansible-galaxy install -r requirements.yml >/dev/null && \
	$(ANSIBLE) ansible-playbook aws-ec2.yml \
		-e node_alias=$(NAME) -e trust_data_version=$(TRUST_DATA_VERSION) \
		-e fl_backend=$(FL_BACKEND) -e trust_num=$(KIT_SLOT) \
		$(KIT_SOURCE_ARGS) \
		$(1)
endef

# Where the FL kit comes from: a directory on this machine holding the slot's own kit (KIT_SRC, for
# a node in another account), the bucket the node was applied with, or neither: the directory tree
# is laid down for the slot and the kit is placed by hand. KIT_SRC is typed, so it comes first.
KIT_SRC ?=
KIT_SOURCE_ARGS = $(if $(KIT_SRC),-e fl_kit_source=local -e fl_kit_local_src=$(abspath $(KIT_SRC)),$(if \
	$(NODE_KIT_BUCKET),-e flip_aicentre_bucket=$(NODE_KIT_BUCKET) -e fl_kit_date=$(FL_KIT_DATE),-e fl_kit_source=precreate \
	-e fl_kit_precreate_slot=Trust_$(KIT_SLOT)))

.PHONY: help init plan apply destroy output ssh-config provision stage-kit seed deploy upgrade shell test lint

help: ## Show this help message
	@echo "FLIP AWS EC2 trust node (KIT=<CODE> HUB=<env>):"
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?##"}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

init: ## Point the root at its state (STATE_BUCKET=, STATE_KEY= default trust-nodes/<NAME>.tfstate)
	@[ -n "$(STATE_BUCKET)" ] || { echo "❌ STATE_BUCKET=<S3 bucket for this node's state> is required."; exit 1; }
	@[ -n "$(NAME)" ] || { echo "❌ NAME= or KIT= is required: the state key is named after the node."; exit 1; }
	$(TF) init -input=false -reconfigure -backend-config="bucket=$(STATE_BUCKET)" \
		-backend-config="key=$(STATE_KEY)" -backend-config="region=$(REGION)"

plan: ## Plan the node (SUBNET_ID= for a site subnet; empty creates a VPC + NAT gateway)
	$(require_kit)
	@[ -f "$(PUBLIC_KEY_FILE)" ] || { echo "❌ PUBLIC_KEY_FILE=$(PUBLIC_KEY_FILE) not found."; exit 1; }
	$(TF) plan -out=node.tfplan $(TF_VARS) -var "public_key=$$(cat $(PUBLIC_KEY_FILE))"

apply: ## Show the saved plan and the target account, then apply it only after a typed yes
	$(TF) show node.tfplan
	@echo ""
	$(call confirm_target,Apply this plan)
	$(TF) apply node.tfplan

destroy: ## Destroy the node, its data and, if created, its network (names the account, then asks)
	$(require_kit)
	@echo "The root volume goes with the node: its OMOP data, XNAT archive and DICOM store cannot be recovered."
	$(call confirm_target,Destroy this node)
	$(TF) destroy $(TF_VARS) -var 'public_key=$(DESTROY_PUBLIC_KEY)'

output: ## Show the root's outputs
	$(TF) output

ssh-config: ## Write the ~/.ssh/config entry that reaches the node over SSM (Host <NAME>)
	python3 scripts/ssh_config.py --alias $(NAME) --instance-id "$$($(TF) output -raw instance_id)" \
		--region $(REGION) --key $(SSH_KEY) $(if $(AWS_PROFILE),--profile $(AWS_PROFILE))

provision: ## Configure the host (aws-ec2.yml), all but the data plays
	$(require_kit)
	$(require_kit_bucket)
	@[ -n "$(TRUST_DATA_VERSION)" ] || { echo "❌ trust/.data_version not found."; exit 1; }
	$(call play,--skip-tags omop-vocab$(,)seed)
	@# The play puts ubuntu in the docker group; a multiplexed ssh connection opened before it keeps
	@# the old groups, and deploy's docker context would then be refused by the socket.
	@ssh -O exit $(NAME) 2>/dev/null || true

stage-kit: ## Stage the FL kit for the trust's registered slot (from the node's kit bucket, or KIT_SRC=<slot kit dir>)
	$(require_kit)
	$(require_kit_bucket)
	@[ -n "$(NODE_KIT_BUCKET)$(KIT_SRC)" ] || { echo "❌ The node was applied with no kit bucket: KIT_SRC=<dir holding this slot's kit> is required."; exit 1; }
	@[ -z "$(KIT_SRC)" ] || [ -d "$(KIT_SRC)" ] || { echo "❌ KIT_SRC=$(KIT_SRC) is not a directory."; exit 1; }
	$(call play,--tags flkit)

# A trust with its own data sets TRUST_DATA_SEED=false in its kit (FLIP#1687): seed then loads no sample data,
# only the vocabulary, so deploy leaves its OMOP and Orthanc as they are. Any other value is refused before deploy
# stops the stack: read as true, it would seed a real trust's OMOP.
TRUST_DATA_SEED_EFFECTIVE = $(call kit_trust_data_seed,$(KIT_FILE))
define require_trust_data_seed
	@case "$(TRUST_DATA_SEED_EFFECTIVE)" in true|false) ;; *) \
		echo "❌ TRUST_DATA_SEED must be true or false in $(notdir $(KIT_FILE)), not '$(TRUST_DATA_SEED_EFFECTIVE)'."; exit 1;; esac
endef
SAMPLE_DATA_OFF = $(filter false,$(TRUST_DATA_SEED_EFFECTIVE))
SEED_SKIP_TAGS = $(if $(NODE_KIT_BUCKET),$(if $(SAMPLE_DATA_OFF),--skip-tags seed),--skip-tags omop-vocab)
# The seed's images at the tags compose runs them at: the kit's OMOP_DB_TAG / ORTHANC_TAG pin, else DOCKER_TAG.
seed_tag = $(or $(call kit_get,$(1)),$(call kit_get,DOCKER_TAG))

# The vocabulary comes from the kit bucket; without one the seed loads the dataset only, as on the
# Azure node, and cohort queries need the vocabulary-free form.
seed: ## Seed OMOP + Orthanc from the canonical dataset (not with TRUST_DATA_SEED=false), then (on a node with a kit bucket) load the vocabulary (stack stopped)
	$(require_kit)
	$(require_trust_data_seed)
	$(require_kit_bucket)
	$(if $(and $(SAMPLE_DATA_OFF),$(if $(NODE_KIT_BUCKET),,off)),@echo "⏭️  Sample data is off for $(KIT) (TRUST_DATA_SEED=false) and there is no KIT_BUCKET for the vocabulary: nothing to load.",$(call play,--tags data $(SEED_SKIP_TAGS) \
		-e omop_postgres_user=$(call kit_get,OMOP_POSTGRES_USER) -e omop_postgres_password=$(call kit_get,OMOP_POSTGRES_PASSWORD) \
		-e omop_postgres_db=$(call kit_get,OMOP_POSTGRES_DB) -e data_access_postgres_password=$(call kit_get,DATA_ACCESS_POSTGRES_PASSWORD) \
		-e orthanc_username=$(call kit_get,ORTHANC_USERNAME) -e orthanc_password=$(call kit_get,ORTHANC_PASSWORD) \
		-e vocab_s3_bucket=$(NODE_KIT_BUCKET) -e omop_db_tag=$(call seed_tag,OMOP_DB_TAG) -e orthanc_tag=$(call seed_tag,ORTHANC_TAG) \
		$(if $(SEED_PROJECTS),-e seed_projects="$(SEED_PROJECTS)") $(if $(SEED_ORTHANC),-e seed_orthanc=$(SEED_ORTHANC)) \
		$(if $(SOURCE_TRUST),-e source_trust=$(SOURCE_TRUST)) $(if $(filter 1,$(FORCE_DICOM_VOCAB)),-e force_dicom_vocab=true)))

# First install: the stack is stopped before the seed and started after it. The kit is read here, on
# this workstation, and the node's daemon is driven over the ssh docker context, so the kit never
# has to exist on the host.
deploy: ## First install: networks, seed with the stack down, then start the trust stack and XNAT
	$(require_kit)
	$(require_trust_data_seed)
	$(require_node_dirs)
	$(require_kit_bucket)
	@docker context inspect $(NAME) >/dev/null 2>&1 || docker context create $(NAME) --docker "host=ssh://$(NAME)"
	@out=$$(docker --context $(NAME) swarm init 2>&1) || case "$$out" in \
		*"already part of a swarm"*) echo "   Swarm already initialised";; \
		*) echo "$$out" >&2; exit 1;; esac
	DOCKER_CONTEXT=$(NAME) $(MAKE) -C $(REPO_ROOT)/trust create-networks create-trust-network KIT=$(KIT) HUB=$(HUB)
	DOCKER_CONTEXT=$(NAME) $(MAKE) -C $(REPO_ROOT)/trust down-trust-ec2 KIT=$(KIT) HUB=$(HUB) 2>/dev/null || true
	$(MAKE) seed KIT=$(KIT) HUB=$(HUB)
	DOCKER_CONTEXT=$(NAME) $(MAKE) -C $(REPO_ROOT)/trust up-trust-ec2 KIT=$(KIT) HUB=$(HUB)

upgrade: ## Move the node to a release tag, data in place (TAG=, FL_TAG=, FORCE=1, YES=1)
	$(require_kit)
	$(require_node_dirs)
	@docker context inspect $(NAME) >/dev/null 2>&1 || docker context create $(NAME) --docker "host=ssh://$(NAME)"
	DOCKER_CONTEXT=$(NAME) $(MAKE) -C $(REPO_ROOT)/trust upgrade-trust KIT=$(KIT) HUB=$(HUB) TAG=$(TAG) FL_TAG=$(FL_TAG) \
		FORCE=$(FORCE) YES=$(YES) ALLOW_CHECKOUT_DRIFT=$(ALLOW_CHECKOUT_DRIFT) NUM_AVAILABLE_GPUS=0

shell: ## Open a shell on the node over SSM
	aws ssm start-session --region $(REGION) --target "$$($(TF) output -raw instance_id)"

# The tests get their own data dir: after `make init` the root's .terraform/ names the S3 backend,
# which `init -backend=false` does not undo, and `terraform test` would then want AWS credentials.
test: ## Credential-free checks: terraform test in the root and its module, plus pytest
	@for d in ../modules/network .; do \
		echo "── $$d"; ( cd $$d && export TF_DATA_DIR=.terraform-test && $(TF) init -backend=false -input=false >/dev/null && $(TF) validate -no-color && $(TF) test -no-color ) || exit 1; \
	done
	uv run --no-project --with pytest --with diagrams pytest tests/ -q

lint: ## terraform fmt check and tflint over the root
	$(TF) fmt -check -recursive . ../modules
	tflint --init --config "$(CURDIR)/.tflint.hcl" && tflint --recursive --config "$(CURDIR)/.tflint.hcl"

, := ,
