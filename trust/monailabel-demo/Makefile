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
# Records the MONAI Label demo video against a running trust (README.md). Run on the trust host:
# the browser runs in a container on the host network, so it reaches XNAT and MONAI Label exactly
# as a clinician's browser on that host would.

.PHONY: demo-video demo-gifs

include ../../deploy/env_mode.mk

KIT ?= GSTT
KIT_FILE := $(if $(wildcard ../.env.$(KIT).$(ENV)),../.env.$(KIT).$(ENV),../.env.$(KIT))
-include $(KIT_FILE)

IMAGE := flip-monailabel-demo:1.63.0
OUT_DIR ?= $(CURDIR)/out

# The password reaches the container through the environment, never the command line.
demo-video: export XNAT_PASSWORD = $(XNAT_ADMIN_PASSWORD)
demo-video: ## Record the demo on XNAT_EXPERIMENT=<session ID> (one CT scan) of trust KIT=<CODE>
	@[ -n "$(XNAT_EXPERIMENT)" ] || { echo "❌ XNAT_EXPERIMENT=<session ID> is required: a CT session with exactly one scan"; exit 1; }
	@[ -n "$(XNAT_PASSWORD)" ] || { echo "❌ no XNAT_ADMIN_PASSWORD from $(KIT_FILE)"; exit 1; }
	docker build -q -t $(IMAGE) .
	mkdir -p $(OUT_DIR)
	docker run --rm --network host --shm-size=2g --user $$(id -u):$$(id -g) -e HOME=/tmp \
	  -e XNAT_URL=http://127.0.0.1:$(XNAT_WEB_PORT) -e XNAT_USER=$(XNAT_ADMIN_USER) -e XNAT_PASSWORD \
	  -e XNAT_EXPERIMENT=$(XNAT_EXPERIMENT) -e MONAI_LABEL_URL=http://127.0.0.1:$(or $(MONAI_LABEL_PORT),8030) \
	  -e DEMO_VIDEO_SCALE=$(or $(DEMO_VIDEO_SCALE),2) -v $(OUT_DIR):/out $(IMAGE)

demo-gifs: ## Cut the docs GIFs (one per chapter) out of the last recording into $(OUT_DIR)/gifs/
	@[ -f "$(OUT_DIR)/result.json" ] || { echo "❌ no recording in $(OUT_DIR): run demo-video first"; exit 1; }
	docker build -q -t $(IMAGE) .
	docker run --rm --user $$(id -u):$$(id -g) -v $(OUT_DIR):/out --entrypoint python3 $(IMAGE) /demo/make_gifs.py
