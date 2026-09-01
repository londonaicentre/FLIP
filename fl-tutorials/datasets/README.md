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

# Tutorial datasets

Shared download/derive/enrich tooling for the FL tutorial datasets. Datasets are
**backend-agnostic** — a tutorial's NVFLARE and Flower twins train on the same data — and
**many-to-one** — one dataset serves several tutorials (spleen alone backs segmentation,
evaluation and diffusion) — so their tooling lives here once instead of duplicated per
backend tree. Everything downloads at run time; nothing is committed.

All outputs land under the shared, gitignored [`fl-tutorials/data/`](../) root, so one
download serves both backends' harnesses (the NVFLARE simulator via each tutorial's
`.env.app`, the Flower compose stack via `flower/run-tutorial.sh`).

Invoke the targets through the fl-tutorials root Makefile (which forwards here):

```bash
make -C fl-tutorials download-xray-data
make -C fl-tutorials download-spleen-data              # MSD build (NUM_CASES=<1-41>, default 10)
make -C fl-tutorials download-spleen-data FL_BACKEND=flower   # pre-built FLIP-format tree
make -C fl-tutorials download-arkplus-finetuning-data  # large (~6.3 GB)
make -C fl-tutorials download-arkplus-eval-data        # (~1.6 GB)
make -C fl-tutorials upload-spleen-labels FLIP_PROJECT_ID=<uuid>   # data enrichment
make -C fl-tutorials download-prostate-data            # PI-CAI (FOLDS="0 1 2 3 4" by default, ~5GB/fold)
make -C fl-tutorials convert-prostate-to-dicom
make -C fl-tutorials convert-prostate-to-nifti
make -C fl-tutorials partition-prostate-data
```

The prostate tutorial needs one more step after partitioning: a **dataset fingerprint** (per-case
voxel spacing, shape after cropping to the non-zero region, foreground intensity statistics) and
the **nnU-Net experiment plan** derived from it (target spacing, patch and batch size,
normalization, U-Net topology). That one is not a Makefile target — it runs from the tutorial's
own uv project, because it reads the partitioned data through `PicaiDataset`:

```bash
cd fl-tutorials/flower/3d_prostate_segmentation
uv sync

# one site
uv run python calculate_dataset_fingerprint_segmentation.py \
  --site-dir ../../data/prostate/sites/RUMC \
  --output-dir configs --modality t2w --num-processes 8 --gpu-memory-GB 8

# or pool several — one fingerprint and one plan over all their studies
uv run python calculate_dataset_fingerprint_segmentation.py \
  --site-dir ../../data/prostate/sites/{ZGT,RUMC,PCNN} \
  --output-dir configs --modality t2w --num-processes 8 --gpu-memory-GB 8
```

Writes `dataset_fingerprint_segmentation.json` + `nnUNetPlans_segmentation.json` into
`--output-dir`. Planning per site yields *different architectures*, so pass every participating
site in one run and give all clients the same plan — see
[`prostate/README.md`](prostate/README.md#nnu-net-plans) for the measured per-center figures.

| Dataset | Source | Output under `fl-tutorials/data/` | Consumed by |
| --- | --- | --- | --- |
| xray | HF `aicentreflip/flip-fl-base-test-data` | `xrays_mini_300/{accession-resources/, dataframe.csv}` | xray_classification (both backends) |
| spleen (MSD build) | MSD Task09_Spleen | `spleen/{images/, dataframe.csv}` | 3d_spleen_segmentation + evaluation + latent_diffusion_model (NVFLARE sim); enrichment labels |
| spleen (FLIP-format) | HF `aicentreflip/flip-fl-base-test-data` | `spleen/{accession-resources/, sample_get_dataframe_response.csv}` + `model_checkpoints/model.pt` | 3d_spleen_segmentation + evaluation (Flower stack) |
| arkplus | HF `aicentreflip/tutorials-arkplus-cxr-classification` | `arkplus/site{1,2}[,_holdoff]/` | the three Ark+ tutorials (NVFLARE) |
| prostate | Zenodo PI-CAI + `picai_labels` (GitHub) | `prostate/{images/, labels/, zonal_labels/, clinical_information/, dicom/, nifti/, sites/<CENTER>/}` | 3d_prostate_segmentation (Flower) |

The two spleen variants coexist in `data/spleen/` — the FLIP-format download removes only
its own outputs, never an MSD build beside it.

## Per-dataset scripts

[`spleen/`](spleen/) owns the spleen scripts and their uv project (`pyproject.toml` — MONAI,
pandas, natsort; `uv.lock` is gitignored):

- `download_spleen_dataset.py` — fetch MSD spleen cases and reorganise each subject to hold
  image + label.
- `create_spleen_accession_csv.py` — build the `accession_id` dataframe the trainers read in
  LOCAL_DEV.
- `upload_spleen_labels_to_xnat.py` — the data-enrichment step: push `label_*.nii.gz` files
  into a real FLIP project's XNAT (see the
  [spleen tutorial README](../nvflare/image_segmentation/3d_spleen_segmentation/README.md)
  for the full walkthrough, and the repo-root `CLAUDE.md` for its `e2e_smoke` wiring). Runs
  against the in-tree `flip-utils`, not `spleen/`'s env.
- `download_spleen_flip_format_dataset.py` — fetch the pre-built FLIP-format tree (fixed
  6-case snapshot) plus the evaluation checkpoint from Hugging Face, replacing only its own
  outputs in `data/spleen/`. A pure Hugging Face fetch, so like the xray/arkplus scripts it
  runs via `uv run --no-project --with huggingface_hub`, not in `spleen/`'s env.

[`xrays_mini_300/`](xrays_mini_300/) owns the single x-ray script — no dedicated uv project,
it runs via `uv run --no-project --with huggingface_hub`, the same way `upload-spleen-labels`
runs against `flip-utils` without adopting `spleen/`'s env:

- `download_xrays_dataset.py` — fetch the Hugging Face snapshot and normalise it into
  `accession-resources/` + `dataframe.csv`.

[`arkplus/`](arkplus/) owns the single arkplus script — no dedicated uv project, it runs via
`uv run --no-project --with huggingface_hub`, the same way `upload-spleen-labels` runs
against `flip-utils` without adopting `spleen/`'s env:

- `download_arkplus_dataset.py` — fetch the given site folders (TRAIN or HOLD-OUT) from
  Hugging Face and normalise each into `accession-resources/` +
  `sample_get_dataframe_response.csv`. Parameterised by `--sites`, so one script backs both
  `download-arkplus-finetuning-data` and `download-arkplus-eval-data`.

[`prostate/`](prostate/) owns the prostate download/preprocessing scripts. The dataset class that
reads this data lives with the tutorial instead, at
`../flower/3d_prostate_segmentation/dataset.py`, as does the nnU-Net planning step above:

- `download_data.py` — fetch the PI-CAI bpMRI images + whole-gland/zonal labels + clinical
  marksheet from Zenodo/GitHub (`FOLDS` narrows which of the 5 ~5GB fold zips to fetch).
- `convert_mha_to_dicom.py` — convert the downloaded `.mha` scans to a DICOM series per study.
- `convert_mha_to_nifti.py` — convert the downloaded `.mha` scans to `.nii.gz`.
- `partition_by_center.py` — split the converted NIfTI scans + labels into one folder per
  acquiring center (RUMC/PCNN/ZGT), ready for `PicaiDataset` per simulated FL client. Re-running
  it repairs stale symlinks, so it is safe over an existing `sites/` tree.
