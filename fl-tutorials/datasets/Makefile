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
#   make -C fl-tutorials download-weights ARCH=squeezenet1_1   # a pretrained backbone a tutorial ships (LDM)
#   make -C fl-tutorials download-spleen-data [NUM_CASES=<1-41>]
#   make -C fl-tutorials download-spleen-checkpoint   # evaluation-tutorial checkpoint only
#   make -C fl-tutorials download-arkplus-finetuning-data
#   make -C fl-tutorials download-arkplus-eval-data
#   make -C fl-tutorials upload-spleen-labels FLIP_PROJECT_ID=<uuid>   # data enrichment
#
# Everything lands under the shared, gitignored fl-tutorials/data/ — see README.md here for
# the per-dataset layout. Every dataset is backend-agnostic: one copy, read by both harnesses.

DATA_DIR := ../data
HF_CACHE := $(DATA_DIR)/.hf

# Hugging Face dataset backing the xray tutorials and the spleen evaluation checkpoint.
HF_TEST_DATA_REPO := aicentreflip/flip-fl-base-test-data
# Hugging Face dataset backing the three Ark+ tutorials (site{1,2} train + hold-out splits).
ARKPLUS_DATA_REPO := aicentreflip/tutorials-arkplus-cxr-classification
# Number of MSD spleen cases to download for the spleen tutorials (1-41).
NUM_CASES ?= 10
# The MSD download/derive scripts run in spleen/'s own uv project (MONAI, pandas, natsort) —
# --project keeps the cwd here while resolving that env. Same shape for the Synthea build.
SPLEEN_UV := uv run --project spleen
SYNTHEA_UV := uv run --project synthea
# The in-tree flip-utils, not the PyPI release, so the tooling always matches this checkout
# (upload-spleen-labels: flip.xnat may be newer here; download-weights: the `full` env is the
# one every tutorial already runs in, so pretrained-weight staging needs no extra project).
FLIP_UTILS_DIR ?= $(abspath ../../flip-utils)
# Pretrained weights land flat under data/weights/ and are then copied into the tutorial's app
# dir by that tutorial's own `make weights` (FLIP#1206) — the download is shared tooling, the
# consumption belongs to the app that ships the file.
WEIGHTS_UV := uv run --project $(FLIP_UTILS_DIR) --extra full
WEIGHTS_DIR := $(DATA_DIR)/weights

.PHONY: download-xray-data download-weights \
	download-spleen-data download-spleen-msd-data \
	download-spleen-checkpoint download-arkplus-finetuning-data \
	download-arkplus-eval-data download-synthea-data upload-spleen-labels \
	fetch-spleen-metadata-table download-spleen-msd-raw convert-spleen-to-dicom \
	create-spleen-metadata-table build-spleen-omop-tables verify-spleen-omop-tables \
	reproduce-spleen-omop build-spleen-canonical verify-spleen-dicom seed-spleen \
	fetch-cxr-metadata-table build-cxr-omop-tables verify-cxr-omop-tables reproduce-cxr-omop \
	download-brain-mri-msd-raw download-brain-mri-data convert-brain-mri-to-dicom \
	create-brain-mri-metadata-table fetch-brain-mri-metadata-table build-brain-mri-omop-tables \
	verify-brain-mri-omop-tables reproduce-brain-mri-omop build-brain-mri-canonical \
	verify-brain-mri-dicom seed-brain-mri

# Download the x-ray reference dataset from Hugging Face and normalise it into the layout
# both harnesses read (data/xrays_mini_300/, gitignored). Idempotent — safe to re-run.
# One canonical CSV name (dataframe.csv): the NVFLARE .env.app files and the Flower
# run-tutorial.sh both point at it. Script + README live in xrays_mini_300/ (see its README).
download-xray-data:
	@uv run --no-project --with huggingface_hub python xrays_mini_300/download_xrays_dataset.py \
		--cache-dir $(HF_CACHE) \
		--output-dir $(DATA_DIR)/xrays_mini_300 \
		--repo-id $(HF_TEST_DATA_REPO)

# A pretrained network a tutorial ships beside its code (FLIP#1206): downloaded ONCE here, with
# torch.hub's own sha256-prefix check, into data/weights/<torchvision filename>. Keyed by
# architecture, not tutorial — a backbone is shared by construction (`weights/fetch_weights.py`
# lists the known ones). Today: `ARCH=squeezenet1_1`, the latent-diffusion perceptual loss.
download-weights:
	@[ -n "$(ARCH)" ] || (echo "❌  ARCH is required. Usage: make download-weights ARCH=squeezenet1_1"; exit 1)
	@$(WEIGHTS_UV) python weights/fetch_weights.py $(ARCH) --out $(WEIGHTS_DIR)

# One spleen dataset for both backends: the MSD build, honouring NUM_CASES, plus the
# checkpoint only the evaluation tutorial needs. The checkpoint comes FIRST because
# download-spleen-msd-data refuses to overwrite an existing data/spleen/images — ordering it
# second would mean an existing MSD build makes the checkpoint unreachable through this target.
download-spleen-data: download-spleen-checkpoint download-spleen-msd-data

# Download the MSD spleen dataset and build its accession CSV (large; NUM_CASES caps the
# subject count). Runs in spleen/'s own uv project for MONAI.
download-spleen-msd-data:
	@echo "⬇️  Downloading MSD spleen dataset ($(NUM_CASES) cases) — large, runs in spleen/'s uv project..."
	@$(SPLEEN_UV) python spleen/download_spleen_dataset.py --output_dir $(DATA_DIR)/spleen/images --num_cases $(NUM_CASES)
	@$(SPLEEN_UV) python spleen/create_spleen_accession_csv.py --images_dir $(DATA_DIR)/spleen/images --output_csv $(DATA_DIR)/spleen/dataframe.csv
	@echo "✅ MSD spleen data ready at $(abspath $(DATA_DIR))/spleen/ — make -C fl-tutorials run-tutorial TUTORIAL=3d_spleen_segmentation"

# Fetch the spleen evaluation-tutorial checkpoint. A pure Hugging Face fetch, so it runs via
# `uv run --no-project --with huggingface_hub` like the xray/arkplus downloads rather than in
# spleen/'s env — that env exists for the MSD scripts' MONAI/torch, which this needs none of.
download-spleen-checkpoint:
	@uv run --no-project --with huggingface_hub python spleen/download_spleen_checkpoint.py \
		--cache-dir $(HF_CACHE) \
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

# Derive the EHR risk-prediction tutorial's tabular dataset (data/synthea/, gitignored) from three
# OMOP tables of the public 1k-person Synthea dataset (AWS Open Data Registry, anonymous HTTPS,
# ~5 MB): one feature row per person plus site1/site2 person_id-modulo splits. Backend-agnostic,
# no FL_BACKEND variant — the NVFLARE .env.app reads the site splits, the Flower stack mounts
# dataframe.csv. Idempotent; the raw tables are cached under data/synthea/.synthea-raw/. Needs
# only pandas, so it runs outside any project venv. On the platform the same data is loaded into
# each trust's OMOP with `make -C trust load-synthea-ehr` (see the tutorial README).
download-synthea-data:
	@echo "⬇️  Downloading Synthea-in-OMOP tabular dataset (AWS Open Data Registry)..."
	@$(SYNTHEA_UV) python synthea/build_synthea_dataframe.py --output-dir $(DATA_DIR)/synthea
	@echo "✅ Synthea data ready at $(abspath $(DATA_DIR))/synthea/ — make -C fl-tutorials run-tutorial TUTORIAL=ehr_risk_prediction"

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
# XNAT, and both backends read the result. SPLEEN_LABELS_DIR points at the one spleen tree both
# backends read, and can be overridden. It runs via `uv run --no-project --with <flip-utils>`
# rather than spleen/'s env, so uploading labels never drags in the simulator's MONAI/torch stack.
SPLEEN_LABELS_DIR ?= $(abspath $(DATA_DIR)/spleen/images)

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
#                                   that actually produced the published OMOP export. No 1.5GB
#                                   download. This is what the verification gate uses.
#   download-spleen-msd-raw -> convert-spleen-to-dicom -> create-spleen-metadata-table
#                                 — the full regeneration path. Deterministic since FLIP#1221 (no
#                                   root, the same bytes on every run), and from that cut the
#                                   published export IS its output, so the two paths agree.
#
# Since FLIP#1221 the spleen DICOM set is NOT published either: MSD Task09 is open data, so like
# brain_mri the set regenerates locally (dicom_output/<subject>/*.dcm) and only the OMOP tables and
# source/dicom_metadata.csv go to aicentreflip/trust-data. build-spleen-canonical / verify-spleen-dicom /
# seed-spleen are the same three steps brain_mri has, on the regenerated tree.
SPLEEN_OMOP_UV := uv run --project datasets/spleen
SPLEEN_DATA := data/spleen
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

# Regeneration path, step 1: NIfTI -> DICOM with synthesised patient identities, on the shared
# pydicom writer (utils/dicom_writer.py — no plastimatch, no root, deterministic; FLIP#1221).
# PYTHONPATH=datasets because the converter imports utils.*, like the OMOP converters below.
convert-spleen-to-dicom:
	@cd .. && PYTHONPATH=datasets $(SPLEEN_OMOP_UV) python datasets/spleen/convert_spleen_dataset.py

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
# Needs no MSD download.
reproduce-spleen-omop: fetch-spleen-metadata-table build-spleen-omop-tables verify-spleen-omop-tables

# The canonical (source_trust) form that gets published and seeded, assembled from the per-trust
# split by trust/omop-db's dataset tool into data/spleen/canonical/spleen_project/, with the metadata
# table beside it (its own directory: publish-trust-data's OMOP_CSV= uploads every CSV under it).
build-spleen-canonical:
	@cd ../../trust/omop-db && uv run python -m omop_db_tools.dataset build \
		--trust-dirs $(FL_TUTORIALS_DIR)/omop/trust_1 $(FL_TUTORIALS_DIR)/omop/trust_2 \
		--dest $(FL_TUTORIALS_DIR)/$(SPLEEN_DATA)/canonical --projects spleen_project
	@mkdir -p ../$(SPLEEN_DATA)/canonical/spleen_project/source \
		&& cp ../data/spleen_metadata.csv ../$(SPLEEN_DATA)/canonical/spleen_project/source/dicom_metadata.csv
	@echo "✅ canonical tables at $(FL_TUTORIALS_DIR)/$(SPLEEN_DATA)/canonical/spleen_project"

# The regenerated DICOM tree against the canonical tables, both ways (publish_dicom.py's pre-publish
# check, without the packaging this project no longer takes).
verify-spleen-dicom:
	@cd ../../trust/orthanc && uv run publish_dicom.py --verify-only --project spleen_project \
		--tables-dir $(FL_TUTORIALS_DIR)/$(SPLEEN_DATA)/canonical --source $(FL_TUTORIALS_DIR)/dicom_output

# Both halves of a running dev trust from the regenerated tree (the OMOP rows from canonical/, the
# instances from dicom_output/). A trust still holding the pre-FLIP#1221 spleen cut needs CLEAN=all
# once (every spleen id changed, so `projects` would leave the old rows beside the new) — pass the
# other projects it holds in PROJECTS so they are reloaded too. DRY_RUN=1 counts the DICOM half only.
seed-spleen:
	@[ -n "$(KIT)" ] || (echo "❌  KIT is required. Usage: make seed-spleen KIT=GSTT [DRY_RUN=1] [CLEAR=1] [CLEAN=all]"; exit 1)
	$(if $(DRY_RUN),@echo "DRY_RUN: skipping seed-omop",$(MAKE) -C ../../trust seed-omop KIT=$(KIT) PROJECTS=spleen_project \
		CANONICAL_DIR=$(FL_TUTORIALS_DIR)/$(SPLEEN_DATA)/canonical $(if $(CLEAN),CLEAN=$(CLEAN)))
	$(MAKE) -C ../../trust seed-orthanc KIT=$(KIT) PROJECTS=spleen_project \
		DICOM_SOURCE=$(FL_TUTORIALS_DIR)/dicom_output TABLES_DIR=$(FL_TUTORIALS_DIR)/$(SPLEEN_DATA)/canonical \
		$(if $(CLEAR),CLEAR=$(CLEAR)) $(if $(DRY_RUN),DRY_RUN=$(DRY_RUN))

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
# brain_mri_project OMOP tables + DICOM regeneration (FLIP#1221)
#
# MSD Task01_BrainTumour (BraTS; 484 four-channel MRI volumes, CC BY-SA 4.0) as four-series MR
# studies. The whole chain is in-tree and needs no root:
#   download-brain-mri-msd-raw -> convert-brain-mri-to-dicom -> create-brain-mri-metadata-table
#     -> build-brain-mri-omop-tables -> build-brain-mri-canonical -> verify-brain-mri-dicom
# and the reproducible path, fetch-brain-mri-metadata-table -> build -> verify, is
# `reproduce-brain-mri-omop` (no download; GATE FAIL until the project's first publish, because the
# gate refuses to compare nothing).
#
# Unlike spleen, the DICOM set is NEVER published: only the OMOP tables and source/dicom_metadata.csv
# go to aicentreflip/trust-data. The converter is deterministic (utils/dicom_writer.py: every UID and
# identity a function of the MSD case id), so the set regenerates byte-for-byte from the MSD tar —
# `make convert-brain-mri-to-dicom METADATA_TABLE=data/brain_mri/source/dicom_metadata.csv` after
# fetch-brain-mri-metadata-table converts exactly the published cohort. Otherwise BRAIN_MRI_NUM_CASES
# (the N lowest-numbered cases; 40 is the published cohort, 20 per trust).
#
# seed-brain-mri KIT=<CODE> seeds a RUNNING dev trust from the local tree, both halves local (the OMOP
# rows from canonical/, the instances from dicom/), so the platform pull can be proven before a data
# version is tagged. After publishing, `make -C trust seed-omop KIT=<CODE> PROJECTS=brain_mri_project`
# loads the same rows from the tag; the DICOM half always comes from the local tree.
BRAIN_MRI_UV := uv run --project datasets/brain_mri
BRAIN_MRI_DATA := data/brain_mri
BRAIN_MRI_NUM_CASES ?= 40
BRAIN_MRI_WORKERS ?= 4
BRAIN_MRI_METADATA_URL := $(HF_TRUST_DATA_BASE)/omop-csv/brain_mri_project/source/dicom_metadata.csv
FL_TUTORIALS_DIR := $(abspath ..)

# Step 0: the tar (7.6 GB, md5-checked, kept beside the extract) and the training half of it —
# data/Task01_BrainTumour/{imagesTr,labelsTr,dataset.json}; the unlabelled imagesTs are not extracted.
download-brain-mri-msd-raw:
	@cd .. && $(BRAIN_MRI_UV) python datasets/brain_mri/download_msd_brain_tumour.py --tar data/Task01_BrainTumour.tar --out-dir data
	@echo "✅ raw MSD extract at fl-tutorials/data/Task01_BrainTumour/imagesTr (484 cases)"

# The simulator layout (LOCAL_DEV): data/brain_mri/images/<accession>/scans/{input_<channel>_<case>,label_<case>}.nii.gz
# + dataframe.csv, the same accessions the DICOM path stamps. NUM_CASES caps it (default 10, as spleen).
download-brain-mri-data: download-brain-mri-msd-raw
	@cd .. && PYTHONPATH=datasets $(BRAIN_MRI_UV) python datasets/brain_mri/prepare_brain_mri_local_data.py \
		--out-dir $(BRAIN_MRI_DATA) --num-cases $(NUM_CASES)
	@echo "✅ brain MRI data ready at $(FL_TUTORIALS_DIR)/$(BRAIN_MRI_DATA)/"

# Step 1: NIfTI -> DICOM, one MR study of four series per case, data/brain_mri/dicom/<accession>/*.dcm.
convert-brain-mri-to-dicom:
	@cd .. && PYTHONPATH=datasets $(BRAIN_MRI_UV) python datasets/brain_mri/convert_brain_mri_to_dicom.py \
		--out-dir $(BRAIN_MRI_DATA)/dicom --workers $(BRAIN_MRI_WORKERS) \
		$(if $(METADATA_TABLE),--metadata-table $(METADATA_TABLE),--num-cases $(BRAIN_MRI_NUM_CASES))

# Step 2: DICOM tree -> data/brain_mri/source/dicom_metadata.csv (one row per series; source_trust decided here).
create-brain-mri-metadata-table:
	@cd .. && PYTHONPATH=datasets $(BRAIN_MRI_UV) python datasets/brain_mri/create_brain_mri_metadata_table.py \
		--dicom-dir $(BRAIN_MRI_DATA)/dicom --output $(BRAIN_MRI_DATA)/source/dicom_metadata.csv

# Reproducible path: the published metadata table at the pinned data version, to the same path.
fetch-brain-mri-metadata-table:
	@cd .. && mkdir -p $(BRAIN_MRI_DATA)/source && curl -fsSL "$(BRAIN_MRI_METADATA_URL)" -o $(BRAIN_MRI_DATA)/source/dicom_metadata.csv
	@echo "✅ metadata table at fl-tutorials/$(BRAIN_MRI_DATA)/source/dicom_metadata.csv (revision $(TRUST_DATA_REVISION))"

# Step 3: metadata table -> omop/brain_mri_project/*.csv + omop/<trust>/brain_mri_project/*.csv (from either path above).
build-brain-mri-omop-tables:
	@cd .. && PYTHONPATH=datasets $(BRAIN_MRI_UV) python datasets/brain_mri/omop_convert_brain_mri.py \
		--metadata $(BRAIN_MRI_DATA)/source/dicom_metadata.csv --omop-root .

# The gate: the generated tables against the published export at the pinned data version.
verify-brain-mri-omop-tables:
	@cd .. && $(BRAIN_MRI_UV) python datasets/utils/verify_omop_tables.py --project brain_mri_project --revision $(TRUST_DATA_REVISION)

reproduce-brain-mri-omop: fetch-brain-mri-metadata-table build-brain-mri-omop-tables verify-brain-mri-omop-tables

# The canonical (source_trust) form that gets published and seeded: the per-trust split assembled by
# trust/omop-db's dataset tool into data/brain_mri/canonical/brain_mri_project/, plus the metadata table
# beside it so the conversion reproduces from the dataset alone. Its own directory on purpose:
# publish-trust-data's OMOP_CSV= uploads every CSV under it.
build-brain-mri-canonical:
	@cd ../../trust/omop-db && uv run python -m omop_db_tools.dataset build \
		--trust-dirs $(FL_TUTORIALS_DIR)/omop/trust_1 $(FL_TUTORIALS_DIR)/omop/trust_2 \
		--dest $(FL_TUTORIALS_DIR)/$(BRAIN_MRI_DATA)/canonical --projects brain_mri_project
	@mkdir -p ../$(BRAIN_MRI_DATA)/canonical/brain_mri_project/source \
		&& cp ../$(BRAIN_MRI_DATA)/source/dicom_metadata.csv ../$(BRAIN_MRI_DATA)/canonical/brain_mri_project/source/
	@echo "✅ canonical tables at $(FL_TUTORIALS_DIR)/$(BRAIN_MRI_DATA)/canonical/brain_mri_project"

# The DICOM tree against the canonical tables, both ways (every accession, study UID and patient present
# on both sides, no accession spanning two studies, no duplicate SOPInstanceUID) — publish_dicom.py's
# pre-publish check, without the packaging step this project never takes.
verify-brain-mri-dicom:
	@cd ../../trust/orthanc && uv run publish_dicom.py --verify-only --project brain_mri_project \
		--tables-dir $(FL_TUTORIALS_DIR)/$(BRAIN_MRI_DATA)/canonical --source $(FL_TUTORIALS_DIR)/$(BRAIN_MRI_DATA)/dicom

# Both halves of a running dev trust from the local tree. DRY_RUN=1 counts the DICOM half and loads
# nothing; CLEAR=1 removes the project's existing studies from Orthanc first.
seed-brain-mri:
	@[ -n "$(KIT)" ] || (echo "❌  KIT is required. Usage: make seed-brain-mri KIT=GSTT [DRY_RUN=1] [CLEAR=1]"; exit 1)
	$(if $(DRY_RUN),@echo "DRY_RUN: skipping seed-omop",$(MAKE) -C ../../trust seed-omop KIT=$(KIT) PROJECTS=brain_mri_project \
		CANONICAL_DIR=$(FL_TUTORIALS_DIR)/$(BRAIN_MRI_DATA)/canonical)
	$(MAKE) -C ../../trust seed-orthanc KIT=$(KIT) PROJECTS=brain_mri_project \
		DICOM_SOURCE=$(FL_TUTORIALS_DIR)/$(BRAIN_MRI_DATA)/dicom TABLES_DIR=$(FL_TUTORIALS_DIR)/$(BRAIN_MRI_DATA)/canonical \
		$(if $(CLEAR),CLEAR=$(CLEAR)) $(if $(DRY_RUN),DRY_RUN=$(DRY_RUN))
