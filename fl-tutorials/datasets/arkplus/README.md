<!--
Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at
    http://www.apache.org/licenses/LICENSE-2.0
Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# Ark+ chest X-ray dataset

Chest X-ray data for the three Ark+ tutorials (NVIDIA FLARE only), downloaded from the Hugging Face dataset
`aicentreflip/tutorials-arkplus-cxr-classification`.

| Splits | Folders | Used by | Make target |
|--------|---------|---------|-------------|
| TRAIN | `site1`, `site2` | [`arkplus_fine_tuning`](../../nvflare/image_classification/arkplus_fine_tuning/README.md) | `download-arkplus-finetuning-data` |
| HOLD-OUT | `site1_holdoff`, `site2_holdoff` | [`arkplus_baseline_classification_evaluation`](../../nvflare/image_evaluation/arkplus_baseline_classification_evaluation/README.md), [`arkplus_multimodel_classification_evaluation`](../../nvflare/image_evaluation/arkplus_multimodel_classification_evaluation/README.md) | `download-arkplus-eval-data` |

## Download

Run from the repository root, through the fl-tutorials Makefile (see [`../README.md`](../README.md)):

```bash
make -C fl-tutorials download-arkplus-finetuning-data  # TRAIN splits, ~6.3 GB
make -C fl-tutorials download-arkplus-eval-data        # HOLD-OUT splits, ~1.6 GB
```

## How it works

Both Make targets call one script, `download_arkplus_dataset.py`, with different `--sites`. It fetches each site folder from Hugging Face and lays it out as:

```text
<site>/
├── accession-resources/
└── sample_get_dataframe_response.csv
```

Only the TRAIN download passes `--write-marker`. Before skipping a re-download, `make reproduce-overhead` in `arkplus_fine_tuning` checks for that marker, not just for the directory.

The download script's only dependency is `huggingface_hub`, so the Make targets run it with `uv run --no-project --with huggingface_hub`. The uv project here (`pyproject.toml`) is for the trust-data chain below.

## On a trust: `arkplus_project`

On the platform the tutorials read the cohorts from a trust's OMOP database and PACS, not from these
folders. The same data is published to `aicentreflip/trust-data` as `arkplus_project`, so a trust loads
it like any other project:

```bash
make -C trust seed KIT=<CODE> PROJECTS=arkplus_project
```

`source_trust` is the dataset's own site: `site1` and its holdout go to partition 1, `site2` and its
holdout to partition 2. Each site is one synthetic cohort from a different generator (the preprint's
UK and Thai cohorts), so unlike `cxr_project`'s alternating split the two partitions are deliberately
not from the same distribution. The split decides `procedure_source_value`: `Chest X-ray` for the
training rows that `arkplus_fine_tuning`'s query selects, `Chest X-ray (holdout)` for the rows both
evaluation queries select.

The chain that builds and publishes it, run from the repository root after both downloads:

```bash
make -C fl-tutorials create-arkplus-metadata-table  # site folders -> data/arkplus/source/dicom_metadata.csv
make -C fl-tutorials build-arkplus-omop-tables      # -> data/arkplus/omop/<trust>/arkplus_project/*.csv
make -C fl-tutorials build-arkplus-canonical        # the source_trust form that gets published
make -C fl-tutorials prepare-arkplus-dicom          # the DICOMs with the platform's required tags filled
make -C fl-tutorials package-arkplus-dicom          # verified against the tables, -> dist/dicom/arkplus_project.tar.gz
make -C fl-tutorials seed-arkplus KIT=<CODE>        # optional: prove the local tree on a running trust first
```

Then `make -C trust publish-trust-data VERSION=<tag> OMOP_CSV=… DICOM=…` and a bump of
`trust/.data_version`. To check the published tables reproduce from the published metadata table
alone: `make -C fl-tutorials reproduce-arkplus-omop`.

| Script | Does |
|--------|------|
| `create_arkplus_metadata_table.py` | One row per accession: DICOM identifiers, folder, split, `source_trust`, the seven labels, and a sex and birth year derived from the AccessionNumber (the DICOMs carry neither) |
| `omop_convert_arkplus.py` | Metadata table -> OMOP tables: person, visit, procedure and image occurrence per accession, and one `image_feature` -> `observation` pair per label (Yes/No). Ids from the project's 6,000,000 block |
| `prepare_arkplus_dicom.py` | Fills `StudyDescription`, `ReferringPhysicianName` and `PatientSex` where empty: imaging-api drops a study without the first two and requires the third |

Trusts loaded before FLIP#2084 hold the same cohorts from an unmerged loader script, with other ids
and no seed marker. Seeding on top of them would load every study twice, so remove those rows first:

```bash
make -C trust remove-stray-studies KIT=<CODE> PROJECTS=arkplus_project DRY_RUN=1  # counts only
make -C trust remove-stray-studies KIT=<CODE> PROJECTS=arkplus_project
make -C trust seed KIT=<CODE> PROJECTS=arkplus_project
```

It matches the project's accession numbers, so it finds exactly those rows (on AICP: 2,340 persons,
one image and seven label observations each). The PACS needs nothing: the published DICOMs are the
same instances, which Orthanc reports as already stored.
