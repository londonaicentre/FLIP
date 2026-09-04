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

# Shared dataset tooling for the FL tutorials. One download per dataset, consumed by BOTH
# backends' harnesses: datasets are backend-agnostic (a tutorial's NVFLARE and Flower twins
# train on the same data), so their download/derive/enrich tooling lives here, once, instead
# of duplicated per backend. The fl-tutorials root Makefile forwards these targets here, so
# the documented entrypoints keep working:
#
#   make -C fl-tutorials download-xray-data
#   make -C fl-tutorials download-spleen-data [NUM_CASES=<1-41>] [FL_BACKEND=flower]
#   make -C fl-tutorials download-arkplus-finetuning-data
#   make -C fl-tutorials download-arkplus-eval-data
#   make -C fl-tutorials upload-spleen-labels FLIP_PROJECT_ID=<uuid>   # data enrichment
#
# Everything lands under the shared, gitignored fl-tutorials/data/ — see README.md here for
# the per-dataset layout. FL_BACKEND matters only where a dataset has per-harness variants
# (spleen: the NVFLARE simulator reads the raw MSD build, the Flower compose stack mounts the
# pre-built FLIP-format tree; both can coexist under data/spleen/).

FL_BACKEND ?= nvflare
DATA_DIR := ../data
HF_CACHE := $(DATA_DIR)/.hf

# Hugging Face dataset backing the xray tutorials and the FLIP-format spleen variant.
HF_TEST_DATA_REPO := aicentreflip/flip-fl-base-test-data
# Hugging Face dataset backing the three Ark+ tutorials (site{1,2} train + hold-out splits).
ARKPLUS_DATA_REPO := aicentreflip/tutorials-arkplus-cxr-classification
# Number of MSD spleen cases to download for the spleen tutorials (1-41).
NUM_CASES ?= 10
# The MSD download/derive scripts run in spleen/'s own uv project (MONAI, pandas, natsort) —
# --project keeps the cwd here while resolving that env.
SPLEEN_UV := uv run --project spleen

.PHONY: download-xray-data download-spleen-data download-spleen-msd-data \
	download-spleen-flip-format-data download-arkplus-finetuning-data \
	download-arkplus-eval-data upload-spleen-labels download-idc-pathology-data

# Download the x-ray reference dataset from Hugging Face and normalise it into the layout
# both harnesses read (data/xrays_mini_300/, gitignored). Idempotent — safe to re-run.
# One canonical CSV name (dataframe.csv): the NVFLARE .env.app files and the Flower
# run-tutorial.sh both point at it. Script + README live in xrays_mini_300/ (see its README).
download-xray-data:
	@uv run --no-project --with huggingface_hub python xrays_mini_300/download_xrays_dataset.py \
		--cache-dir $(HF_CACHE) \
		--output-dir $(DATA_DIR)/xrays_mini_300 \
		--repo-id $(HF_TEST_DATA_REPO)

# The spleen dataset has two variants sharing data/spleen/: the raw MSD build (images/ +
# dataframe.csv — what the NVFLARE simulator reads, and where the enrichment labels come
# from) and the pre-built FLIP-format tree (accession-resources/ +
# sample_get_dataframe_response.csv — what the Flower compose stack mounts). The plain
# download-spleen-data entrypoint keeps its historical per-backend meaning; call the
# explicit variant targets directly to fetch a specific one.
ifeq ($(FL_BACKEND),flower)
download-spleen-data: download-spleen-flip-format-data
else
download-spleen-data: download-spleen-msd-data
endif

# Download the MSD spleen dataset and build its accession CSV (large; NUM_CASES caps the
# subject count). Runs in spleen/'s own uv project for MONAI.
download-spleen-msd-data:
	@echo "⬇️  Downloading MSD spleen dataset ($(NUM_CASES) cases) — large, runs in spleen/'s uv project..."
	@$(SPLEEN_UV) python spleen/download_spleen_dataset.py --output_dir $(DATA_DIR)/spleen/images --num_cases $(NUM_CASES)
	@$(SPLEEN_UV) python spleen/create_spleen_accession_csv.py --images_dir $(DATA_DIR)/spleen/images --output_csv $(DATA_DIR)/spleen/dataframe.csv
	@echo "✅ MSD spleen data ready at $(abspath $(DATA_DIR))/spleen/ — make -C fl-tutorials run-tutorial TUTORIAL=3d_spleen_segmentation"

# Download the pre-built FLIP-format spleen tree (fixed 6-case snapshot; ignores NUM_CASES)
# plus the evaluation-tutorial checkpoint. Removes only its own outputs, so an MSD build in
# the same data/spleen/ survives a re-download. A pure Hugging Face fetch, so it runs via
# `uv run --no-project --with huggingface_hub` like the xray/arkplus downloads rather than in
# spleen/'s env — that env exists for the MSD scripts' MONAI/torch, which this needs none of.
download-spleen-flip-format-data:
	@uv run --no-project --with huggingface_hub python spleen/download_spleen_flip_format_dataset.py \
		--cache-dir $(HF_CACHE) \
		--output-dir $(DATA_DIR)/spleen \
		--checkpoint-dir $(DATA_DIR)/model_checkpoints \
		--repo-id $(HF_TEST_DATA_REPO)

# Download the Ark+ chest X-ray TRAIN splits from Hugging Face and normalise them into the
# layout arkplus_fine_tuning/.env.app expects (data/arkplus/site{1,2}/, gitignored).
# Idempotent — safe to re-run. Large (~6.3 GB). Script + README live in arkplus/ (see its
# README) — the same script backs download-arkplus-eval-data below, parameterised by --sites.
download-arkplus-finetuning-data:
	@uv run --no-project --with huggingface_hub python arkplus/download_arkplus_dataset.py \
		--cache-dir $(HF_CACHE) \
		--output-dir $(DATA_DIR)/arkplus \
		--repo-id $(ARKPLUS_DATA_REPO) \
		--sites site1 site2 \
		--label training \
		--next-step "make -C fl-tutorials run-tutorial TUTORIAL=arkplus_fine_tuning" \
		--write-marker

# Download the Ark+ chest X-ray HOLD-OUT splits from Hugging Face and normalise them into the
# layout the two Ark+ evaluation tutorials' .env.app expect (data/arkplus/site{1,2}_holdoff/,
# gitignored). Idempotent — safe to re-run (~1.6 GB).
download-arkplus-eval-data:
	@uv run --no-project --with huggingface_hub python arkplus/download_arkplus_dataset.py \
		--cache-dir $(HF_CACHE) \
		--output-dir $(DATA_DIR)/arkplus \
		--repo-id $(ARKPLUS_DATA_REPO) \
		--sites site1_holdoff site2_holdoff \
		--label hold-out \
		--next-step "make -C fl-tutorials run-tutorial TUTORIAL=arkplus_baseline_classification_evaluation (or arkplus_multimodel_classification_evaluation)"

# NCI Imaging Data Commons digital pathology: DICOM Slide Microscopy whole-slide images plus their
# Pan-Cancer-Nuclei-Seg bulk annotations, partitioned into FLIP sites by TCGA tissue source site.
#
# Downloads straight from IDC's public buckets -- no credentials, and no DICOM is re-hosted anywhere.
# SLIDES_PER_SITE and SITES tune the subset; the defaults pull ~2.1 GB, chosen because base-resolution
# instances in this collection run to 3 GB each (median ~890 MB) and the default sites are the two with
# the most slides under the size cap.
IDC_SITES ?= A8,A7
IDC_SLIDES_PER_SITE ?= 5
IDC_MAX_SLIDE_MB ?= 400

download-idc-pathology-data:
	@uv run --no-project --with idc-index --with pandas python idc_pathology/prepare_idc_pathology.py \
		--resolve \
		--sites $(IDC_SITES) \
		--slides-per-site $(IDC_SLIDES_PER_SITE) \
		--max-slide-mb $(IDC_MAX_SLIDE_MB) \
		--out-dir $(DATA_DIR)/idc_pathology
	@echo ""
	@echo "Next step: make -C fl-tutorials run-tutorial TUTORIAL=idc_pathology_nuclei_detection_evaluation"

# Data enrichment: upload the MSD spleen labels into a real FLIP project's XNAT, so the spleen
# apps find a label_*.nii.gz beside each pulled input_*.nii.gz. Needs NUM_CASES=41 on the MSD
# download above to cover every mapped accession.
#
# Enrich EVERY Trust in the project, not one: each Trust's XNAT holds only its own studies, so a
# Trust left without labels fails training at the zero-pairs guard. One invocation covers the
# roster — pass a space-separated XNAT_URLS (credentials from XNAT_USER/XNAT_PASS), or repeat
# XNAT_CREDENTIALS_FILES for per-Trust logins. With neither, it falls back to a single server from
# XNAT_HOST/XNAT_USER/XNAT_PASS. TRUST=N filters the manifest by the OMOP source_trust column and
# is rarely needed: the whole mapping goes to every server and each ignores the others' studies.
#
# The script is single-copy and backend-agnostic — enrichment happens once per FLIP project, in
# XNAT, and both backends read the result. Only the label source differs per variant, so
# SPLEEN_LABELS_DIR defaults by FL_BACKEND (MSD build vs FLIP-format tree) and can be overridden.
# It runs via `uv run --no-project --with <flip-utils>` rather than spleen/'s env, so uploading
# labels never drags in the simulator's MONAI/torch stack.
SPLEEN_LABELS_DIR ?= $(abspath $(if $(filter flower,$(FL_BACKEND)),$(DATA_DIR)/spleen/accession-resources,$(DATA_DIR)/spleen/images))
# upload-spleen-labels runs against the in-tree flip-utils, not the PyPI release, so the enrichment
# CLI always matches this checkout (flip.xnat may be newer here than the last published version).
FLIP_UTILS_DIR ?= $(abspath ../../flip-utils)

upload-spleen-labels:
	@[ -n "$(FLIP_PROJECT_ID)" ] || (echo "❌  FLIP_PROJECT_ID is required. Usage: make upload-spleen-labels FLIP_PROJECT_ID=<uuid> [XNAT_URLS=\"http://host1 http://host2\"] [DRY_RUN=1]"; exit 1)
	@uv run --no-project --with $(FLIP_UTILS_DIR) python spleen/upload_spleen_labels_to_xnat.py \
		--flip-project-id $(FLIP_PROJECT_ID) \
		--labels-dir $(SPLEEN_LABELS_DIR) \
		$(if $(TRUST),--trust $(TRUST)) \
		$(foreach url,$(XNAT_URLS),--xnat-url $(url)) \
		$(foreach cfg,$(XNAT_CREDENTIALS_FILES) $(XNAT_CREDENTIALS_FILE),--credentials-file $(cfg)) \
		$(if $(OVERWRITE),--overwrite) \
		$(if $(ALLOW_NO_OP),--allow-no-op) \
		$(if $(REQUIRE_FULL_COVERAGE),--require-full-coverage) \
		$(if $(DRY_RUN),--dry-run)
