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

# NVFLARE tutorial runner. Each tutorial under <category>/<name>/ carries a Makefile
# + .env.app whose `make run` delegates to `make sim` — the tutorial's job.py driving a
# FLIP recipe on the NVFLARE simulator (SimEnv), run in the flip-utils venv with the
# `full` ML extra. Requires a GPU, and the tutorial's dataset downloaded first (see each
# tutorial's README); `make export` inside a tutorial needs neither.
#
#   make list-tutorials
#   make run-tutorial TUTORIAL=xray_classification
#   make run-all-tutorials      # runs every tutorial (heavy; stops on first failure)
#
# Dataset downloads live in fl-tutorials/datasets/ (shared with the Flower harness), and land
# under the shared gitignored fl-tutorials/data/ where each tutorial's .env.app points —
# `make -C fl-tutorials download-<dataset>-data` (see fl-tutorials/datasets/README.md).

# Discover tutorials: any <category>/<name>/ that carries a .env.app.
TUTORIAL_DIRS := $(sort $(patsubst %/,%,$(dir $(wildcard */*/.env.app))))

.PHONY: list-tutorials run-tutorial run-all-tutorials

list-tutorials:
	@echo "Available NVFLARE tutorials (pass the name to 'make run-tutorial TUTORIAL=…'):"
	@for d in $(TUTORIAL_DIRS); do echo "  - $$(basename $$d)  ($$d)"; done

run-tutorial:
	@dir="$(strip $(foreach d,$(TUTORIAL_DIRS),$(if $(filter $(TUTORIAL),$(notdir $(d))),$(d))))"; \
	if [ -z "$(TUTORIAL)" ] || [ -z "$$dir" ]; then \
		echo "Set TUTORIAL=<name>. Available:"; \
		for d in $(TUTORIAL_DIRS); do echo "  - $$(basename $$d)"; done; \
		exit 1; \
	fi; \
	echo "🚀 Running NVFLARE tutorial '$(TUTORIAL)' ($$dir)"; \
	$(MAKE) -C "$$dir" run

run-all-tutorials:
	@for d in $(TUTORIAL_DIRS); do \
		echo "🚀 Running NVFLARE tutorial $$(basename $$d)"; \
		$(MAKE) -C "$$d" run || { echo "❌ tutorial $$(basename $$d) failed"; exit 1; }; \
	done

