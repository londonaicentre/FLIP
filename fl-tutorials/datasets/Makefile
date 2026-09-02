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
# PI-CAI folds to fetch for download-prostate-data (each fold zip is ~5GB).
FOLDS ?= 0 1 2 3 4
# The PI-CAI download/convert/partition scripts run in prostate/'s own uv project (MONAI,
# nibabel, SimpleITK, einops, torch) — --project keeps the cwd here while resolving that env.
PROSTATE_UV := uv run --project prostate

.PHONY: download-xray-data download-spleen-data download-spleen-msd-data \
	download-spleen-flip-format-data download-arkplus-finetuning-data \
	download-arkplus-eval-data upload-spleen-labels \
	fetch-spleen-metadata-table download-spleen-msd-raw convert-spleen-to-dicom \
	create-spleen-metadata-table build-spleen-omop-tables verify-spleen-omop-tables \
	reproduce-spleen-omop \
	fetch-cxr-metadata-table build-cxr-omop-tables verify-cxr-omop-tables reproduce-cxr-omop \
	download-prostate-data convert-prostate-to-dicom convert-prostate-to-nifti partition-prostate-data \
	create-prostate-metadata-table fetch-prostate-metadata-table build-prostate-omop-tables \
	verify-prostate-omop-tables reproduce-prostate-omop build-prostate-canonical package-prostate-dicom

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

# Download the PI-CAI bpMRI dataset (images + whole-gland/zonal labels + clinical marksheet)
# and convert/partition it for the 3d_prostate_segmentation tutorial (Flower). Script + README
# live in prostate/ (see its README). Runs in prostate/'s own uv project for MONAI/SimpleITK.
# FOLDS narrows the download for a tutorial-sized cohort, e.g. FOLDS="0". Idempotent — skips
# folds/labels already downloaded.
download-prostate-data:
	@echo "⬇️  Downloading PI-CAI dataset (folds: $(FOLDS)) — large, runs in prostate/'s uv project..."
	@DATA_DIR=$(abspath $(DATA_DIR))/prostate FOLDS="$(FOLDS)" $(PROSTATE_UV) python prostate/download_data.py
	@echo "✅ PI-CAI data ready at $(abspath $(DATA_DIR))/prostate/ — make -C fl-tutorials convert-prostate-to-dicom"

# Convert the downloaded .mha scans to a DICOM series per study, so they can be pulled into a
# trust's PACS the same way any other study would be. Only the axial series the tutorial reads by
# default: the sagittal/coronal T2W PI-CAI also ships are unused and would double the DICOM tree
# (and the published set). PROSTATE_MODALITIES="t2w adc hbv sag cor" converts everything.
PROSTATE_MODALITIES ?= t2w adc hbv
convert-prostate-to-dicom:
	@$(PROSTATE_UV) python prostate/convert_mha_to_dicom.py \
		--input $(DATA_DIR)/prostate/images --output $(DATA_DIR)/prostate/dicom \
		--modalities $(PROSTATE_MODALITIES)

# Convert the DICOM series (above) to .nii.gz with the platform's own pinned dcm2niix image —
# the same conversion an fl-client gets from XNAT after image pull, so the simulator trains on
# identical bytes. Scanner metadata rides in the DICOM and comes back as a BIDS sidecar; there
# is no second .mha → NIfTI path to drift. Needs Docker. Runs after convert-prostate-to-dicom.
convert-prostate-to-nifti:
	@$(PROSTATE_UV) python prostate/convert_dicom_to_nifti.py \
		--input $(DATA_DIR)/prostate/dicom --output $(DATA_DIR)/prostate/nifti

# Partition the converted NIfTI scans + labels by acquiring center (RUMC/PCNN/ZGT), ready for
# PicaiSegDataset per simulated FL client. Needs convert-prostate-to-nifti to have run first.
partition-prostate-data:
	@$(PROSTATE_UV) python prostate/partition_by_center.py --data-dir $(DATA_DIR)/prostate

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
# The published tables are read at a data-version TAG on aicentreflip/trust-data: the dataset holds
# one copy of every table at omop-csv/<project>/, and the version is the revision, never the path.
# HF_TRUST_DATA_REVISION overrides; otherwise the pin — trust/.data_version once FLIP#1101 lands
# (one tag for the whole dataset), trust/omop-db/.data_version until then.
TRUST_DATA_REVISION ?= $(or $(HF_TRUST_DATA_REVISION),$(shell cat ../../trust/.data_version 2>/dev/null),$(shell cat ../../trust/omop-db/.data_version 2>/dev/null))
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

# ---------------------------------------------------------------------------------------------
# prostate_project OMOP tables + DICOM set (the seed pipeline's third dataset — FLIP#1100 follow-on)
#
# The first dataset published with no snapshot at all: the trusts load it through `make -C trust
# seed`, by the source_trust column the metadata table decides from the scanner vendor (Siemens ->
# trust 1, Philips -> trust 2). Full chain, from the fold download:
#   download-prostate-data -> convert-prostate-to-dicom -> create-prostate-metadata-table
#     -> build-prostate-omop-tables -> build-prostate-canonical -> package-prostate-dicom
#     -> make -C trust publish-trust-data VERSION=... OMOP_CSV=... DICOM=...
# Reproducible path (no 5 GB download, no conversion), the same shape as spleen's and cxr's:
#   fetch-prostate-metadata-table -> build-prostate-omop-tables -> verify-prostate-omop-tables
# or `reproduce-prostate-omop` for the three. Everything lands under DATA_DIR/prostate/.
PROSTATE_DATA := $(abspath $(DATA_DIR))/prostate
PROSTATE_OMOP_UV := uv run --project datasets/prostate
PROSTATE_METADATA_URL := $(HF_TRUST_DATA_BASE)/omop-csv/prostate_project/source/dicom_metadata.csv
PROSTATE_MARKSHEET_URL := $(HF_TRUST_DATA_BASE)/omop-csv/prostate_project/source/marksheet.csv

# DICOM tree -> source/dicom_metadata.csv (one row per series, source_trust decided here) +
# source/marksheet.csv (the studies present). These two are the published inputs of the conversion.
create-prostate-metadata-table:
	@$(PROSTATE_UV) python prostate/create_prostate_metadata_table.py --dicom $(PROSTATE_DATA)/dicom \
		--marksheet $(PROSTATE_DATA)/clinical_information/marksheet.csv --output $(PROSTATE_DATA)/source

# Reproducible path: the published inputs at the pinned data version.
fetch-prostate-metadata-table:
	@mkdir -p $(PROSTATE_DATA)/source \
		&& curl -fsSL "$(PROSTATE_METADATA_URL)" -o $(PROSTATE_DATA)/source/dicom_metadata.csv \
		&& curl -fsSL "$(PROSTATE_MARKSHEET_URL)" -o $(PROSTATE_DATA)/source/marksheet.csv
	@echo "✅ prostate source tables at $(PROSTATE_DATA)/source (revision $(TRUST_DATA_REVISION))"

# source/ -> omop/prostate_project/*.csv (every row, trust column) + omop/<trust>/prostate_project/.
# PYTHONPATH=datasets: the converter imports utils.*, as spleen's and cxr's do.
build-prostate-omop-tables:
	@cd .. && PYTHONPATH=datasets $(PROSTATE_OMOP_UV) python datasets/prostate/omop_convert_prostate.py \
		--metadata $(PROSTATE_DATA)/source/dicom_metadata.csv --marksheet $(PROSTATE_DATA)/source/marksheet.csv \
		--omop-root $(PROSTATE_DATA)

# The faithfulness gate, same script as spleen/cxr.
verify-prostate-omop-tables:
	@cd .. && $(PROSTATE_OMOP_UV) python datasets/utils/verify_omop_tables.py --project prostate_project \
		--generated-dir $(PROSTATE_DATA)/omop --revision $(TRUST_DATA_REVISION)

reproduce-prostate-omop: fetch-prostate-metadata-table build-prostate-omop-tables verify-prostate-omop-tables

# The canonical (source_trust) form that gets published: the per-trust split assembled by
# trust/omop-db's dataset tool, plus the two source tables beside it so the conversion reproduces
# from the dataset alone. This directory is what publish-trust-data's OMOP_CSV= takes.
build-prostate-canonical:
	@cd ../../trust/omop-db && uv run python -m omop_db_tools.dataset build \
		--trust-dirs $(PROSTATE_DATA)/omop/trust_1 $(PROSTATE_DATA)/omop/trust_2 \
		--dest $(PROSTATE_DATA)/canonical --projects prostate_project
	@mkdir -p $(PROSTATE_DATA)/canonical/prostate_project/source && cp \
		$(PROSTATE_DATA)/source/dicom_metadata.csv $(PROSTATE_DATA)/source/marksheet.csv \
		$(PROSTATE_DATA)/canonical/prostate_project/source/
	@echo "✅ canonical tables at $(PROSTATE_DATA)/canonical/prostate_project"

# The DICOM set, verified both ways against the canonical tables just built (the pre-publish
# check: every accession, study UID and patient present on both sides), then packaged as the
# dicom/prostate_project.tar.gz that publish-trust-data's DICOM= takes.
package-prostate-dicom:
	@cd ../../trust/orthanc && uv run publish_dicom.py --project prostate_project \
		--tables-dir $(PROSTATE_DATA)/canonical --source $(PROSTATE_DATA)/dicom \
		--out $(PROSTATE_DATA)/dist/dicom/prostate_project.tar.gz
