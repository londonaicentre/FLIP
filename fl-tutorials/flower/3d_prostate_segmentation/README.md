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

# Federated 3D prostate zonal segmentation with nnU-Net planning, MONAI and Flower

This tutorial trains a 3D U-Net to outline the **prostate gland and its two zones** (peripheral
zone, PZ, and transition zone, TZ) on T2-weighted MRI from the public
[PI-CAI](https://pi-cai.grand-challenge.org/) dataset, across several FLIP trusts. It differs from
the [spleen tutorial](../3d_spleen_segmentation/README.md) in two ways worth learning from:

- **The network is planned, not hand-picked.** nnU-Net's *experiment planner* measures the cohort
  (voxel spacing, image shape, foreground intensities) and derives the U-Net topology, patch size
  and normalisation from it. The plan it writes is committed with the app and both the server and
  every client build the same network from it (`app/models.py`). Because the three PI-CAI centres
  scan at different resolutions, **planning per site yields different architectures** whose
  weights cannot be averaged — so the plan is generated once, pooled over every site, and shipped
  to all of them. See [nnU-Net plans](#nnu-net-plans).
- **A study is three scans and the app must pick one.** Each accession pulls a T2W, an ADC and a
  high-b DWI series, all with the same masks beside them after enrichment. `app/data_loading.py`
  selects the requested series from the XNAT export layout rather than assuming one image per
  study. See [Data on the platform](#data-on-the-platform).

The training recipe is nnU-Net's (deep-supervised Dice + binary cross-entropy, SGD with polynomial decay,
patch-based training with nnU-Net's augmentations), ported from the standalone trainer this tutorial
grew out of — see [Where the reference trainer went](#where-the-reference-trainer-went).

## Folder structure

```shell
3d_prostate_segmentation
├── app/                                    # FLAT: everything here is uploaded to the platform
│   ├── client_app.py                       # Flower ClientApp: cohort -> pull -> train_seg -> reply
│   ├── server_app.py                       # byte-copy of fl-apps/flower/standard (FedAvg + FLIP status)
│   ├── strategy.py                         # byte-copy of fl-apps/flower/standard (per-client metrics)
│   ├── models.py                           # the plan JSON -> MONAI DynUNet; get_model()
│   ├── task.py                             # plan geometry, DS loss, optimiser/scheduler, per-volume eval
│   ├── data_loading.py                     # FLIP_BASE + select_series() + build_dataset()
│   ├── dataset.py                          # affine-matched image/mask loading (build_loader, PicaiDataset)
│   ├── preprocess.py                       # pad/crop/normalise, augmentations, the patch iterator
│   ├── train_helpers.py                    # train_seg — the training epoch, as ported
│   ├── nnUNetPlans_segmentation.json       # the pooled nnU-Net plan (make plan); COMMITTED
│   ├── config.json                         # job_type + MODALITY + TRAIN (train_seg's conf)
│   └── config.toml                         # platform run-config overrides (rounds, lr, best-model)
├── calculate_dataset_fingerprint_segmentation.py   # nnU-Net fingerprint + planner (host-side, needs nnunetv2)
├── nnunet_train.py, nnunet_infer.py, network.py    # REFERENCE ONLY: the standalone trainer the app was ported from
├── Makefile                                # make plan
├── pyproject.toml                          # flwr app config for the simulator (+ `planning` dependency group)
├── query.sql                               # the cohort: every PI-CAI MRI series, with ISUP / csPCa / PI-RADS
└── README.md
```

`app/` has no sub-folders on purpose: the platform's upload is non-recursive, and `.md` files are
not uploadable, which is why the plan's provenance is recorded here rather than beside the JSON.

## Running this tutorial

### Fast local iteration: the flwr simulator (`make sim-tutorial`)

No containers; `flwr run` in flip-utils' environment, two simulated sites slicing one dev cohort.
When the host has a GPU each site gets an equal share of it (`SIM_NUM_GPUS=<fraction>` overrides,
`SIM_NUM_GPUS=0` keeps the CPU — about ten times slower for this network).

```bash
# 1. PI-CAI fold 0 (5 GB) -> the simulator layout for the first NUM_CASES studies (~1 min per 10)
make -C fl-tutorials prepare-prostate-local-data NUM_CASES=12
# 2. two rounds of one epoch (pyproject.toml's defaults)
make -C fl-tutorials sim-tutorial TUTORIAL=3d_prostate_segmentation FL_BACKEND=flower
```

Keep `NUM_CASES` at 10 or more: each simulated site gets roughly half the studies and the 20 % / 20 %
validation and test splits must each land at least one study per site. Results (final and best
global model, `cross_val_results.json`) go under `fl-services/flower/runs/<model-id>/training_outputs/`.

### On the platform

The cohort is `prostate_project`, published on `aicentreflip/trust-data` and seeded into the dev
trusts with `make -C trust seed-trusts PROJECTS=prostate_project` (one PI-CAI centre per trust:
ZGT → trust 1, PCNN → trust 2). The masks are **not** in OMOP — a segmentation has nowhere to live in a
cohort query — so they reach the project as **data enrichment**: after the image pull,
`upload-prostate-labels` writes the whole-gland mask (`label_*`) and the zonal mask (`zonal_*`)
into every scan's `NIFTI` resource on every trust's XNAT. The smoke does all of it:

```bash
make -C fl-tutorials download-prostate-data FOLDS=0         # the labels + zonal labels (+ images)
make -C flip-api e2e_smoke_prostate FL_BACKEND=flower        # cohort -> pull -> enrichment -> training -> download
```

Standalone enrichment, outside the smoke (both dev trusts; `DRY_RUN=1` to check first):

```bash
make -C fl-tutorials upload-prostate-labels FLIP_PROJECT_ID=<uuid> \
  XNAT_URLS="http://127.0.0.1:8105 http://127.0.0.1:8107" DRY_RUN=1
```

Or upload `app/` by hand in the UI (`query.sql` is the cohort), run the enrichment, confirm it, and
train. `app/config.toml` carries the platform's round budget.

## Data on the platform

`query.sql` selects every PI-CAI MRI series in OMOP — **one row per series, three per study**, all
sharing an `accession_id` — together with the marksheet's ISUP grade group, clinically significant
cancer flag and highest lesion PI-RADS, so a cohort can be narrowed on them. The app pulls each study
**once** and then has to pick the T2W series out of the three files it gets back. `select_series` in
`app/data_loading.py` tries, in order:

1. the scan folder: XNAT exports `…/scans/<scan id>-<description>/resources/NIFTI/files/`, and the
   scan id is the DICOM SeriesNumber the dataset stamps (1 = T2W, 2 = ADC, 3 = high-b DWI);
2. a `_t2w` token in the file name (the naming of the simulator's own data);
3. a single candidate, whatever it is called;

and raises, listing the files, when none of these singles one out — a broken pull must not become a
coin toss. The masks are then the `label_` / `zonal_` siblings of the chosen image. Image and masks
are paired **by affine, not by array index** (`app/dataset.py`): XNAT's dcm2niix stores volumes with
the row axis reversed relative to the `.mha`-derived masks, and reading both through `Orientationd`
puts them on one grid (`tests/test_prostate_dataset_orientation.py` pins it).

The simulator reads the same shape from disk: `prepare-prostate-local-data` writes
`data/prostate/images/<accession>/scans/<n>-<Description>/{input,label,zonal}_<accession>_<mod>.nii.gz`
straight from the PI-CAI download and a `dataframe.csv` with `query.sql`'s columns, so the series
selection and mask pairing exercised locally are the ones that run on a trust.

## nnU-Net plans

Training is configured by a **dataset fingerprint** (per-case spacing, cropped shape and foreground
intensity statistics) and the **experiment plan** derived from it (target spacing, patch and batch
size, normalisation, U-Net topology). `calculate_dataset_fingerprint_segmentation.py` — nnU-Net v2's
extractor and planner, adapted — produces both from the partitioned site folders:

```bash
uv sync --group planning          # nnunetv2 and its dependencies, for the planner only
make plan                         # pooled over data/prostate/sites/{ZGT,PCNN,RUMC}; copies the plan into app/
make plan PLAN_SITES=../../data/prostate/sites/RUMC   # one site, for comparison
```

`make plan` needs the dcm2niix-converted site tree
(`download-prostate-data` → `convert-prostate-to-dicom` → `convert-prostate-to-nifti` →
`partition-prostate-data`; the NIfTI step runs the platform's own dcm2niix image in Docker). Without
Docker, `prepare-prostate-local-data PROSTATE_SITE_TREE=data/prostate/local_sites` lays a
SimpleITK-converted tree out in the same shape, and `make plan PLAN_SITES=…/local_sites/*` gives
the same plan: the fingerprint reads only zooms, cropped shapes and intensities, none of which depend
on the storage order.

**Generate the plan once, pooled, and give every site the same file.** Planned per site over the
full cohort (`t2w`, `--gpu-memory-GB 8`):

| site | studies | median spacing (d, h, w) | median shape | patch size |
| ---- | ------- | ------------------------ | ------------ | ---------- |
| ZGT  | 350 | `[3.0, 0.5, 0.5]`   | `[21, 383, 383]`  | `[14, 256, 224]` |
| RUMC | 800 | `[3.6, 0.5, 0.5]`   | `[19, 383, 383]`  | `[12, 192, 192]` |
| PCNN | 350 | `[3.0, 0.34, 0.34]` | `[27, 1024, 672]` | `[10, 352, 224]` |
| all three pooled | 1500 | `[3.0, 0.5, 0.5]` | `[21, 383, 383]` | `[10, 192, 160]` |

ZGT and RUMC land on the same topology, but PCNN — the highest in-plane resolution — keeps stage 3
anisotropic (`kernel_sizes` `[1, 3, 3]` where the others have `[3, 3, 3]`), which changes the shape
of the convolution weights: a client planned on PCNN cannot have its updates aggregated with one
planned on ZGT. Which site is the odd one out shifts with how many studies each contributes, so it
cannot be predicted from the centre alone. Pooling is a *planning-time* pooling of shape and
intensity statistics only; no imaging leaves its site during training.

**Provenance of the committed plan** (`app/nnUNetPlans_segmentation.json`), regenerated 2026-09-23:
the canonical dcm2niix route over the **full PI-CAI public cohort** — `download-prostate-data`
(all five folds) → `convert-prostate-to-dicom` (t2w/adc/hbv) → `convert-prostate-to-nifti`
(`ghcr.io/londonaicentre/xnat-dcm2niix:v1.0.20260724`) → `partition-prostate-data` → `make plan`
pooled over `sites/{ZGT,PCNN,RUMC}` = 350 + 350 + 800 = 1500 studies, `--modality t2w`,
`--gpu-memory-GB 8`. Result: median spacing `[3.0, 0.5, 0.5]`, median shape `[21, 383, 383]`, patch
`[10, 192, 160]`, six stages `[32, 64, 128, 256, 320, 320]`, kernels `[1,3,3] [1,3,3] [3,3,3] ×4`,
strides `[1,1,1] [1,2,2] [1,2,2] [2,2,2] [1,2,2] [1,2,2]`, foreground mean/std 215.6 / 120.4 — the
pooled row of the table above. As a DynUNet that is 30.2 M parameters with four auxiliary outputs.
Record the route, cohort, budget and date here whenever the plan is regenerated.

### From the plan to a MONAI network

The platform's FL images carry MONAI but not `nnunetv2`, and an app must not install packages at run
time, so `app/models.py` builds the network with MONAI instead: `NnUNetDynUNet`, a subclass of
MONAI's `DynUNet`, from the plan's `arch_kwargs` — `kernel_sizes` → `kernel_size`, `strides` →
`strides` (and `strides[1:]` → `upsample_kernel_size`), `features_per_stage` → `filters`,
`conv_bias` → `conv_bias`, instance norm and leaky ReLU as planned, and `n_stages - 2` →
`deep_supr_num` (nnU-Net supervises every decoder resolution but the lowest). A plan DynUNet cannot
build faithfully — a residual encoder, a stage with other than two convolutions — raises rather than
being approximated.

## Metrics and best-model selection

Per client and round: `train_loss`, `val_loss`, `val_dice_mean` and the per-zone `val_dice_wg`,
`val_dice_pz`, `val_dice_tz` (whole gland, PZ, TZ), each also as a per-epoch series
(`<name>@epoch.x_<N>`). The evaluate phase scores the test split **per volume** with sliding-window
inference (`test_loss`, `test_dice_mean`, `test_dice_wg/pz/tz`); note that the training and validation
Dice `train_seg` reports are per **patch** averages, so the two are not directly comparable. The
aggregated `test_dice_mean` selects the best global model (`best_FL_global_model.pt`, alongside the
final one) — `best-model-metric` in `config.toml`.

## Differential privacy

Same mechanism as the spleen tutorial: `flip.flower.privacy.flip_local_dp_mod` clips each client's
update to `dp-clipping-norm` and adds Gaussian noise calibrated to (`dp-epsilon`, `dp-delta`) before
the reply leaves the SuperNode; `dp-enabled = false` switches it off. DynUNet with affine instance
norm has only floating-point tensors, so every array is privatised.

## Where the reference trainer went

`nnunet_train.py`, `nnunet_infer.py` and `network.py` stay at the tutorial root as the standalone
(non-federated) trainer this app was ported from. They are not runnable on the platform — they need
`nnunetv2` and read site folders rather than a FLIP cohort.

| standalone | federated app |
| --- | --- |
| `nnunet_train.py` plan reading (`PlansManager`, `possible_patch_size`) | `task.plan_geometry` |
| `network.build_network_architecture` (nnU-Net `PlainConvUNet`) | `models.build_dynunet_from_plan` (`NnUNetDynUNet`, the same network on MONAI's `DynUNet`) |
| `nnunetv2` `DeepSupervisionWrapper` | `task.DeepSupervisionLoss` |
| `build_augmentations`, `PatchIterd(..., mode="wrap")` | `preprocess.build_augmentations`, `preprocess.build_patch_iter` |
| SGD + `PolynomialLR` over `epochs` | `task.build_optimizer`, `task.build_scheduler` (decay spans `rounds × local-epochs`) |
| `train_helpers.train_seg` | unchanged, minus the GPU-only assumptions |
| `EarlyStopping` | none — best-model selection on the server takes its place |
| `nnunet_infer.py` / `inference_func` | `task.evaluate_func` (sliding window, per-volume Dice) |
