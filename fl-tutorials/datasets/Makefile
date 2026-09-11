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
	download-arkplus-eval-data upload-spleen-labels download-idc-pathology-data \
	fetch-idc-pathology-manifest build-idc-pathology-omop verify-idc-pathology-omop-tables \
	reproduce-idc-pathology-omop seed-idc-pathology upload-idc-pathology-annotations \
	fetch-spleen-metadata-table download-spleen-msd-raw convert-spleen-to-dicom \
	create-spleen-metadata-table build-spleen-omop-tables verify-spleen-omop-tables \
	reproduce-spleen-omop \
	fetch-cxr-metadata-table build-cxr-omop-tables verify-cxr-omop-tables reproduce-cxr-omop

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
# SLIDES_PER_SITE and SITES tune the subset; the defaults pull ~6.4 GB (24 slides, 12 per site, plus their
# annotations). The size cap matters because base-resolution instances in this collection run to 3 GB each
# (median ~890 MB); the default sites are the two with the most slides under it. Twelve per site rather
# than five because a trust releases no row-level data for a cohort below COHORT_QUERY_THRESHOLD (10).
IDC_SITES ?= A8,A7
IDC_SLIDES_PER_SITE ?= 12
IDC_MAX_SLIDE_MB ?= 400

# The slide selection is a lockfile, and it is published rather than committed: it lives on
# aicentreflip/trust-data at omop-csv/pathology_project/source/manifest.csv -- where the cxr project
# keeps the metadata table its OMOP export was built from -- at the data version trust/.data_version
# pins, and is fetched into the gitignored data root. So every developer downloads the same slides
# even as IDC issues new index releases, and the repository carries no data file. IDC_RESOLVE=1
# re-selects instead, writing a fresh manifest to the same place; that goes out as a NEW data version
# (publish-trust-data, then bump the pin), which is the review point for a dataset change.
IDC_MANIFEST = $(DATA_DIR)/idc_pathology/manifest.csv
IDC_MANIFEST_URL = $(HF_TRUST_DATA_BASE)/omop-csv/pathology_project/source/manifest.csv

fetch-idc-pathology-manifest:
	@mkdir -p $(dir $(IDC_MANIFEST)) && curl -fsSL "$(IDC_MANIFEST_URL)" -o $(IDC_MANIFEST)
	@echo "✅ manifest at $(abspath $(IDC_MANIFEST)) (revision $(TRUST_DATA_REVISION))"

download-idc-pathology-data:
	@$(if $(IDC_RESOLVE),:,$(MAKE) --no-print-directory fetch-idc-pathology-manifest)
	@uv run --no-project --with idc-index --with pandas python idc_pathology/prepare_idc_pathology.py \
		$(if $(IDC_RESOLVE),--resolve --sites $(IDC_SITES) --slides-per-site $(IDC_SLIDES_PER_SITE) \
			--max-slide-mb $(IDC_MAX_SLIDE_MB) --manifest-out $(IDC_MANIFEST),--manifest $(IDC_MANIFEST)) \
		--out-dir $(DATA_DIR)/idc_pathology
	@$(MAKE) --no-print-directory build-idc-pathology-omop
	@echo ""
	@echo "Next step: make -C fl-tutorials run-tutorial TUTORIAL=idc_pathology_nuclei_detection_evaluation"

# Manifest -> omop/<trust>/pathology_project/*.csv, the same per-trust layout the spleen and cxr
# chains write (gitignored; verified against the published export, never committed). Runs from
# fl-tutorials/ like the other chains so omop/ lands in the one place the gate reads. The builder is
# deterministic -- same manifest in, byte-identical tables out -- which is what the gate certifies.
build-idc-pathology-omop:
	@cd .. && uv run --no-project --with pandas python datasets/idc_pathology/build_omop_project.py \
		--manifest data/idc_pathology/manifest.csv --out omop

# Diff the generated tables against the published export for the pinned data version. Same gate
# script as spleen and cxr, selected by --project; tables a project does not publish are skipped.
verify-idc-pathology-omop-tables:
	@cd .. && uv run --no-project --with pandas python datasets/utils/verify_omop_tables.py \
		--project pathology_project --revision $(TRUST_DATA_REVISION)

# The whole reproducible path in one command: fetch the published manifest, derive, verify. Needs no
# slide download.
reproduce-idc-pathology-omop: fetch-idc-pathology-manifest build-idc-pathology-omop verify-idc-pathology-omop-tables

# Seed the pathology slides and their OMOP rows into RUNNING dev trusts, so the tutorial can be
# driven through the platform (cohort query -> imaging pull -> XNAT -> viewer) rather than only in
# the simulator. Additive on purpose: update-orthanc-data / update-omop-data replace a trust's whole
# data directory from a prepared tarball and cannot add to a live one.
#
# Two halves, keyed on the same published manifest at the same tag so they agree by construction:
# - OMOP rows: the platform's own seed pipeline (make -C trust seed-omop, FLIP#1101), which fetches
#   the published omop-csv/pathology_project/ tables and loads each kit's source_trust slice. Its
#   DICOM half (seed-orthanc) is NOT used -- it expects dicom/<project>.tar.gz on the dataset, and
#   re-hosting the slides is exactly what this tutorial avoids.
# - Slides: posted into each trust's Orthanc from the local IDC download by seed_slides.py.
#
# IDC_KITS names the kits seed-omop runs for; IDC_TRUSTS is "<n>=<orthanc-url>[,<user>:<pass>]" per
# trust, defaulting to the shipped dev roster's published ports. Credentials come from the kit
# files, which are gitignored, so they are read at run time rather than baked in here.
IDC_KITS ?= GSTT KCH
IDC_TRUST_1 ?= 1=http://127.0.0.1:8042,$(ORTHANC_AUTH_1)
IDC_TRUST_2 ?= 2=http://127.0.0.1:8044,$(ORTHANC_AUTH_2)
IDC_TRUSTS ?= $(IDC_TRUST_1) $(IDC_TRUST_2)

# Pull each value out of its kit file at expansion time; a missing kit yields an empty string and the
# seeder then fails on connect rather than seeding the wrong place.
kit_value = $(shell sed -n 's/^$(2)=//p' ../../trust/.env.$(1).development 2>/dev/null | head -1)
ORTHANC_AUTH_1 ?= $(call kit_value,GSTT,ORTHANC_USERNAME):$(call kit_value,GSTT,ORTHANC_PASSWORD)
ORTHANC_AUTH_2 ?= $(call kit_value,KCH,ORTHANC_USERNAME):$(call kit_value,KCH,ORTHANC_PASSWORD)

seed-idc-pathology: fetch-idc-pathology-manifest
	@$(if $(DRY_RUN),echo "⏭️  DRY_RUN=1 -- skipping the OMOP half (make -C trust seed-omop has no dry run)",\
		for kit in $(IDC_KITS); do \
			$(MAKE) -C ../../trust seed-omop KIT=$$kit PROJECTS=pathology_project || exit 1; \
		done)
	@uv run --no-project --with requests python idc_pathology/seed_slides.py \
		--manifest $(IDC_MANIFEST) \
		$(foreach t,$(IDC_TRUSTS),--trust "$(t)") \
		$(if $(DRY_RUN),--dry-run)

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
# The enrichment uploaders run against the in-tree flip-utils, not the PyPI release, so the
# enrichment CLI always matches this checkout (flip.xnat may be newer here than the last
# published version). Shared by upload-spleen-labels and upload-idc-pathology-annotations.
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

# Data enrichment: upload the IDC pathology reference annotations into a real FLIP project's XNAT,
# so the nuclei-detection evaluation app finds an annotation.dcm beside each pulled slide.
#
# This is NOT optional polish -- it is how the annotations reach a Trust at all. XNAT's DICOM
# receiver (dcm4che 2.0.29) has no entry for the Microscopy Bulk Simple Annotations SOP class, so a
# C-STORE of one is refused; and because slide and annotation share an accession, leaving the
# annotation in Orthanc fails the whole study's C-MOVE and the slide never arrives either. So
# seed-idc-pathology seeds slides only and this target delivers the annotations over XNAT's REST
# API, which negotiates no presentation contexts. See idc_pathology/upload_annotations_to_xnat.py.
#
# Enrich EVERY Trust in the project: each Trust's XNAT holds only its own studies. Pass a
# space-separated XNAT_URLS (credentials from XNAT_USER/XNAT_PASS), or repeat XNAT_CREDENTIALS_FILES
# for per-Trust logins. TRUST=N filters the manifest by its site partition and is rarely needed.
upload-idc-pathology-annotations:
	@[ -n "$(FLIP_PROJECT_ID)" ] || (echo "❌  FLIP_PROJECT_ID is required. Usage: make upload-idc-pathology-annotations FLIP_PROJECT_ID=<uuid> [XNAT_URLS=\"http://host1 http://host2\"] [DRY_RUN=1]"; exit 1)
	@uv run --no-project --with $(FLIP_UTILS_DIR) python idc_pathology/upload_annotations_to_xnat.py \
		--flip-project-id $(FLIP_PROJECT_ID) \
		--data-dir $(DATA_DIR)/idc_pathology \
		$(if $(TRUST),--trust $(TRUST)) \
		$(foreach url,$(XNAT_URLS),--xnat-url $(url)) \
		$(foreach cfg,$(XNAT_CREDENTIALS_FILES) $(XNAT_CREDENTIALS_FILE),--credentials-file $(cfg)) \
		$(if $(OVERWRITE),--overwrite) \
		$(if $(ALLOW_NO_OP),--allow-no-op) \
		$(if $(REQUIRE_FULL_COVERAGE),--require-full-coverage) \
		$(if $(DRY_RUN),--dry-run)

# The OMOP-generation chains (FLIP#1092): spleen below, then cxr. Every vendored script uses
# hardcoded, cwd-relative paths inherited from its original repo, so all these targets run from
# fl-tutorials/ (one level up) rather than from here — that is the only directory where `data/…`,
# `tables/` and `dicom_output/` resolve as the scripts expect. Do not "simplify" either chain by
# dropping the `cd ..`.
#
# Spleen OMOP-generation chain
#
# There are two ways to reach build-spleen-omop-tables:
#   fetch-spleen-metadata-table   — the reproducible path. Downloads the published metadata table
#                                   that actually produced the published OMOP export. No root, no
#                                   1.5GB download. This is what the verification gate uses.
#   download-spleen-msd-raw -> convert-spleen-to-dicom -> create-spleen-metadata-table
#                                 — the full regeneration path. Needs root and re-synthesises
#                                   patient identities, so its output will NOT match the published
#                                   export (see the README). Use it to exercise the DICOM stage.
SPLEEN_OMOP_UV := uv run --project datasets/spleen
# The published tables are read at omop-csv/<project>/ on a data-version TAG on aicentreflip/trust-data
# — the FLIP#1101 layout, one copy of every table with the version as a git tag. The pre-#1101 copies
# at omop-csv/<version>/<project>/ are still published but nothing in the repo reads them since
# FLIP#1101; both forms resolve, and the versioned-path ones go once stag/prod have bumped. See the
# note above PIN_FILES in utils/verify_omop_tables.py.
# HF_TRUST_DATA_REVISION overrides; otherwise the pin in trust/.data_version, one tag for the
# whole dataset.
TRUST_DATA_REVISION ?= $(or $(HF_TRUST_DATA_REVISION),$(shell cat ../../trust/.data_version 2>/dev/null))
HF_TRUST_DATA_BASE := https://huggingface.co/datasets/aicentreflip/trust-data/resolve/$(TRUST_DATA_REVISION)
SPLEEN_METADATA_URL := $(HF_TRUST_DATA_BASE)/omop-csv/spleen_project/source/dicom_metadata.csv

# Reproducible path: fetch the canonical metadata table for the pinned data version.
fetch-spleen-metadata-table:
	@cd .. && mkdir -p data && curl -fsSL "$(SPLEEN_METADATA_URL)" -o data/spleen_metadata.csv
	@echo "✅ metadata table at fl-tutorials/data/spleen_metadata.csv (revision $(TRUST_DATA_REVISION))"

# Regeneration path, step 0: the RAW MSD extract. NOTE this is NOT `download-spleen-data`, which
# reorganises the archive into the FLIP accession-resources layout and then rmtree's the raw tree;
# convert_spleen_dataset.py reads the RAW layout (data/Task09_Spleen/imagesTr). Both downloads are
# legitimate — they serve different consumers.
download-spleen-msd-raw:
	@cd .. && $(SPLEEN_OMOP_UV) python -c "from monai.apps.utils import download_and_extract; \
		download_and_extract(url='https://msd-for-monai.s3-us-west-2.amazonaws.com/Task09_Spleen.tar', \
		filepath='data/Task09_Spleen.tar', output_dir='data')"
	@echo "✅ raw MSD extract at fl-tutorials/data/Task09_Spleen/imagesTr (41 cases)"

# Regeneration path, step 1: NIfTI -> DICOM with synthesised patient identities.
# WORKSTATION ONLY: pyplastimatch's install_precompiled_binaries() copies to /usr/local/bin, so this
# needs root and is never run in CI. Run as:
#   sudo $(shell which uv) run --project datasets/spleen python datasets/spleen/convert_spleen_dataset.py
# from fl-tutorials/. Create the venv as your normal user first (any other target does it) — a sudo
# run can leave a root-owned .venv that breaks every later non-sudo invocation.
convert-spleen-to-dicom:
	@cd .. && $(SPLEEN_OMOP_UV) python datasets/spleen/convert_spleen_dataset.py

# Regeneration path, step 2: DICOM -> tables/dicom_metadata.csv, then bridge it to where the OMOP
# converter expects its input. The copy stands in for a step that was manual upstream:
# create_metadata_table.py came from flip_project_spleen_segmentation and writes
# tables/dicom_metadata.csv, while omop_convert_spleen.py came from flip-omop-mock-data and reads
# data/spleen_metadata.csv. A human moved the file between the two repos. Automating that is the
# point of having both halves in one tree; editing either script's hardcoded path would break the
# byte-faithfulness the verification gate depends on.
create-spleen-metadata-table:
	@cd .. && $(SPLEEN_OMOP_UV) python datasets/spleen/create_metadata_table.py \
		&& cp tables/dicom_metadata.csv data/spleen_metadata.csv

# Metadata table -> omop/<trust>/spleen_project/*.csv, ready for trust/omop-db's build_canonical +
# import_tables. Reads data/spleen_metadata.csv, from either path above.
#
# PYTHONPATH=datasets is required because omop_convert_spleen.py imports utils.omop_mappings:
# `python datasets/spleen/x.py` puts datasets/spleen on sys.path, never datasets/.
build-spleen-omop-tables:
	@cd .. && PYTHONPATH=datasets $(SPLEEN_OMOP_UV) python datasets/spleen/omop_convert_spleen.py

# Diff the generated tables against the published export for the pinned data version. Re-run this
# after a .data_version bump, or after any change to the converter or the shared schemas.
verify-spleen-omop-tables:
	@cd .. && $(SPLEEN_OMOP_UV) python datasets/utils/verify_omop_tables.py --project spleen_project --revision $(TRUST_DATA_REVISION)

# The whole reproducible path in one command: fetch the canonical input, convert, verify.
# Needs no root and no MSD download.
reproduce-spleen-omop: fetch-spleen-metadata-table build-spleen-omop-tables verify-spleen-omop-tables

# ---------------------------------------------------------------------------------------------
# cxr_project OMOP tables (FLIP#1092 wave 2)
#
# Only the OMOP conversion is in-tree. The chest X-rays are generated by a synthetic model in the
# private londonaicentre/xraycat repo, so there is no in-tree equivalent of the spleen regeneration
# path -- the provenance chain here starts at the published DICOM metadata table:
#   fetch-cxr-metadata-table -> build-cxr-omop-tables -> verify-cxr-omop-tables
# or `reproduce-cxr-omop` for all three. Needs no root and no image download.
CXR_OMOP_UV := uv run --project datasets/cxr
CXR_METADATA_URL := $(HF_TRUST_DATA_BASE)/omop-csv/cxr_project/source/dicom_metadata.csv

# Fetch the canonical metadata table for the pinned data version. This is the table that actually
# produced the published cxr_project export -- its 8332 rows yield exactly the published person_ids.
fetch-cxr-metadata-table:
	@cd .. && mkdir -p data && curl -fsSL "$(CXR_METADATA_URL)" -o data/cxr_metadata.csv
	@echo "✅ metadata table at fl-tutorials/data/cxr_metadata.csv (revision $(TRUST_DATA_REVISION))"

# Metadata table -> omop/<trust>/cxr_project/*.csv, ready for trust/omop-db's build_canonical +
# import_tables. PYTHONPATH=datasets for the same reason as the spleen target: the converter imports
# utils.omop_mappings, and `python datasets/cxr/x.py` puts datasets/cxr on sys.path, never datasets/.
build-cxr-omop-tables:
	@cd .. && PYTHONPATH=datasets $(CXR_OMOP_UV) python datasets/cxr/omop_convert_cxr.py

# Diff the generated tables against the published export for the pinned data version. Same gate
# script as spleen, selected by --project; tables a project does not publish are skipped.
verify-cxr-omop-tables:
	@cd .. && $(CXR_OMOP_UV) python datasets/utils/verify_omop_tables.py --project cxr_project --revision $(TRUST_DATA_REVISION)

reproduce-cxr-omop: fetch-cxr-metadata-table build-cxr-omop-tables verify-cxr-omop-tables
