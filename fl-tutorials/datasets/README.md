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

[`cxr/`](cxr/) owns the `cxr_project` OMOP converter and its uv project (`pyproject.toml` —
pandas, pandera, sqlglot, tqdm; `uv.lock` is gitignored). It has no download script: the images
come from the private `londonaicentre/xraycat`, not from a public dataset. See
[OMOP mock-data generation](#omop-mock-data-generation-flip1092) below.

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
- `convert_dicom_to_nifti.py` — convert those DICOM series to `.nii.gz` with the platform's own
  pinned dcm2niix image (read from `trust/xnat/xnat/config/dcm2niix_command.json`), so the
  simulator trains on the same bytes an fl-client gets from XNAT. Needs Docker.
- `partition_by_center.py` — split the converted NIfTI scans + labels into one folder per
  acquiring center (RUMC/PCNN/ZGT), ready for `PicaiDataset` per simulated FL client. Re-running
  it repairs stale symlinks, so it is safe over an existing `sites/` tree.
## OMOP mock-data generation (FLIP#1092)

The mock OMOP CDM data that backs the tutorials — and the trust `omop-db` seed data it feeds —
is generated in-tree, per dataset. Two projects are covered so far: `spleen_project` (the whole
chain, from the MSD download) and `cxr_project` (the OMOP conversion only; see below for why).
Both share one contract in [`utils/`](utils/) and one verification gate.

### Spleen: the full chain

Generated by a four-step chain vendored from two upstream repos
(`flip_project_spleen_segmentation` and `flip-omop-mock-data`) plus the MSD download itself:

1. **Download** the raw MSD Task09_Spleen archive (`download-spleen-msd-raw` — NOT the same
   download as `download-spleen-data` above; this one reads/writes the raw MSD layout
   `data/Task09_Spleen/imagesTr`, not the FLIP accession-resources layout).
2. **NIfTI → DICOM** (`convert-spleen-to-dicom`), synthesising patient identities so the
   output is realistic mock data rather than real MSD provenance.
3. **DICOM → metadata table** (`create-spleen-metadata-table`), writing
   `tables/dicom_metadata.csv` and copying it to `data/spleen_metadata.csv` where the next
   step expects it — this copy stands in for a manual step upstream.
4. **Metadata table → OMOP tables** (`build-spleen-omop-tables`), writing
   `omop/<trust>/spleen_project/*.csv`.

There is also a **reproducible path** that skips steps 1-2 entirely:
`fetch-spleen-metadata-table` downloads the *published* metadata table for the pinned
`trust/omop-db/.data_version` (no root, no 1.5GB MSD download) straight into
`data/spleen_metadata.csv`, ready for step 4. `reproduce-spleen-omop` chains
`fetch-spleen-metadata-table` → `build-spleen-omop-tables` → `verify-spleen-omop-tables` in
one command. Because step 2 re-synthesises patient identities on every run, the full
regeneration path (steps 1-3) will **not** reproduce the published export byte-for-byte —
use it to exercise the DICOM stage, not to check faithfulness. `verify-spleen-omop-tables`
(`utils/verify_omop_tables.py --project spleen_project`) is the faithfulness check: it diffs the
locally generated tables against the published ones for the pinned data version and prints a
`MATCH`/`DIFF` per table, exiting non-zero on any divergence. Re-run it after a `.data_version`
bump or after any change to the converter or the shared schemas. The recorded run is in
[`spleen/VERIFICATION.md`](spleen/VERIFICATION.md).

```bash
make -C fl-tutorials fetch-spleen-metadata-table   # reproducible path, step 1
make -C fl-tutorials build-spleen-omop-tables       # reproducible path, step 2
make -C fl-tutorials verify-spleen-omop-tables      # faithfulness gate
make -C fl-tutorials reproduce-spleen-omop          # the three above, chained
make -C fl-tutorials download-spleen-msd-raw        # regeneration path, step 1 (large)
make -C fl-tutorials convert-spleen-to-dicom        # regeneration path, step 2 (root/workstation only)
make -C fl-tutorials create-spleen-metadata-table   # regeneration path, step 3
```

**Output feeds `trust/omop-db`**: the generated `omop/<trust>/spleen_project/*.csv` tables
are exactly the per-trust layout `trust/omop-db`'s `build_canonical` (assembles the canonical
dataset from per-trust project directories) and `import_tables` (loads a trust's slice into
its OMOP database) expect as input — see `trust/omop-db/README.md`.

**`convert-spleen-to-dicom` is workstation-only, and needs root**:
`pyplastimatch`'s `install_precompiled_binaries()` copies binaries to `/usr/local/bin`, so it
is never run in CI. Create the venv as your normal user first (any other target in this
directory does that) and only then run the conversion itself under sudo:

```bash
sudo $(which uv) run --project datasets/spleen python datasets/spleen/convert_spleen_dataset.py
```

from `fl-tutorials/` — the same `datasets/spleen` project/script paths the Make recipe uses,
since `convert_spleen_dataset.py`'s hardcoded cwd-relative paths only resolve one level up
from this directory (see the chain's header comment in `Makefile`). Running `uv` itself under
sudo before the venv exists leaves a root-owned `.venv`, which then breaks every later
non-sudo invocation of any target in this Makefile — if that happens,
`rm -rf spleen/.venv` and let a normal-user run recreate it.

### CXR: the OMOP conversion only

[`cxr/`](cxr/) carries `omop_convert_cxr.py` and its own uv project. Only the **conversion** is
here: the chest X-rays themselves are generated by a synthetic model that lives outside this
repo, in [`londonaicentre/xraycat`](https://github.com/londonaicentre/xraycat) (private — org
members only), along with the DICOM write and the metadata extraction. That is the scope
FLIP#1092 set, since those images do not come from a public dataset the way MSD spleen does.

So the provenance chain recorded here starts at the DICOM metadata table, published beside its
outputs at `omop-csv/cxr_project/source/dicom_metadata.csv` on `aicentreflip/trust-data`, read at
the pinned data-version tag.
There is no regeneration path to offer and no root needed:

```bash
make -C fl-tutorials fetch-cxr-metadata-table       # the published canonical input
make -C fl-tutorials build-cxr-omop-tables          # -> omop/<trust>/cxr_project/*.csv
make -C fl-tutorials verify-cxr-omop-tables         # faithfulness gate
make -C fl-tutorials reproduce-cxr-omop             # the three above, chained
```

The recorded run is in [`cxr/VERIFICATION.md`](cxr/VERIFICATION.md).

Two shape differences from spleen, both inherited from what the dataset is:

- cxr publishes **`observation`** where spleen publishes **`measurement`**. Spleen's per-image
  features are DICOM tags (slice thickness and the like), so they are measurements; cxr's are
  findings read out of a synthetic radiology report, so each becomes an `image_feature` paired with
  an `observation` carrying an explicit yes/no. A negated finding is published as a "no", not as a
  missing row.
- cxr's `image_feature_id` / `observation_id` are **derived, not allocated** — the
  `image_occurrence_id` with a two-digit finding index appended, which puts them around 100,000,000,
  outside every project's reserved block in `utils/omop_ids.py`. Nothing collides today, but do not
  assume that band is reserved. It is annotated at the line that builds it.

### The shared contract

[`utils/`](utils/) holds what every dataset's converter agrees on, and nothing that is a property
of one dataset:

- `omop_schemas.py` — the OMOP CDM 5.4 table schemas, cached as committed YAML under
  `utils/schemas/` and regenerated from the upstream DDL with `python omop_schemas.py --regenerate`
  (the only path that needs network).
- `omop_mappings.py` — concept-ID mappings, plus the handful of scalar concept ids more than one
  converter uses.
- `omop_ids.py` — the per-project surrogate-key blocks (`cxr_project` 1M, `spleen_project` 2M,
  `prostate_project` 3M). All projects load into the same trust database, so these must not
  collide. `person_id` is deliberately *not* blocked: it derives from a random NHS number.
- `verify_omop_tables.py` — the verification gate, shared because nothing in it is dataset-specific.
  `--project` selects which published export to diff against; tables a project does not publish are
  skipped, and a run that compares *nothing* fails rather than passing vacuously.

Converters import this as `utils.*`, which is why the Make recipes set `PYTHONPATH=datasets` when
invoking them — `python datasets/<name>/x.py` puts `datasets/<name>` on `sys.path`, never
`datasets/`.

What deliberately does *not* live here: the DICOM tags a dataset mocks, and the anatomy it depicts.
Those are properties of the dataset. Spleen *mocks* `Manufacturer` and `InstitutionName`; prostate
(FLIP#1091) carries real ones recovered from the PI-CAI `.mha` headers.
