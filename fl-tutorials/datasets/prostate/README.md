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

# prostate

Downloads the [PI-CAI](https://pi-cai.grand-challenge.org/) bpMRI dataset
([Zenodo record 6624726](https://zenodo.org/records/6624726)) and its
whole-gland + zonal (PZ/TZ) segmentation labels
([picai_labels](https://github.com/DIAGNijmegen/picai_labels)), converts the `.mha` scans to
DICOM (so they can be pulled into a trust's PACS the same way any other study would be) and to
NIfTI, and partitions the converted data by acquiring center for the `3d_prostate_segmentation`
tutorial (Flower). Download + preprocessing only — the `PicaiSegDataset` class that reads this
partitioned data lives with the tutorial itself, at
[`../../flower/3d_prostate_segmentation/picai_dataset.py`](../../flower/3d_prostate_segmentation/picai_dataset.py).

Dedicated uv project (`pyproject.toml` — SimpleITK, tqdm; `uv.lock` is gitignored), the same
pattern as [`../spleen/`](../spleen/).

## Folder structure

```shell
prostate
├── download_data.py        # Downloads PI-CAI images + whole-gland/zonal labels + clinical marksheet
├── convert_mha_to_dicom.py # Converts .mha scans to a DICOM series per study
├── convert_mha_to_nifti.py # Converts .mha scans to .nii.gz
├── partition_by_center.py  # Splits nifti/labels by acquiring center (RUMC/PCNN/ZGT)
└── README.md
```

## Invoking

Via the fl-tutorials root Makefile (see [`../README.md`](../README.md)):

```bash
make -C fl-tutorials download-prostate-data          # FOLDS="0 1 2 3 4" by default, ~5GB/fold
make -C fl-tutorials convert-prostate-to-dicom
make -C fl-tutorials convert-prostate-to-nifti
make -C fl-tutorials partition-prostate-data
```

`FOLDS` narrows the download for a tutorial-sized cohort, e.g. `FOLDS="0"`. Data lands under
`fl-tutorials/data/prostate/`: `images/` (one
`<patient_id>/<patient_id>_<study_id>_<modality>.mha` per scan, modalities `t2w`/`adc`/`hbv`),
`labels/` (`<patient_id>_<study_id>.nii.gz` whole-gland masks, AI-derived per
[Bosma et al., 2022](https://grand-challenge.org/algorithms/prostate-segmentation/)),
`zonal_labels/` (`<patient_id>_<study_id>.nii.gz` peripheral/transition zone masks —
`1`=PZ, `2`=TZ — AI-derived,
[HeviAI23](https://github.com/DIAGNijmegen/picai_labels/tree/main/anatomical_delineations/zonal_pz_tz/AI/HeviAI23);
picai_labels has no human-expert zonal delineations for this cohort), and
`clinical_information/marksheet.csv` (per-study clinical fields — PSA, PI-RADS, ISUP, csPCa —
plus the acquiring `center`: `RUMC`, `PCNN`, or `ZGT`). Labels and marksheet all come from the
same `picai_labels` archive, so one download covers all three. Re-running the download skips
folds/labels/zonal labels/clinical info already downloaded (marked by a `.done` file dropped in
`images/`/`labels/`/`zonal_labels/`/`clinical_information/` after a successful extract).

`convert_mha_to_dicom.py` is adapted from [picai_prep](https://github.com/DIAGNijmegen/picai_prep),
which converts DICOM to `.mha` via SimpleITK; it runs that conversion in reverse, writing one
`.dcm` file per slice with correct `PatientID`, `StudyInstanceUID`, `SeriesInstanceUID`,
`ImagePositionPatient`, `ImageOrientationPatient`, `PixelSpacing`, and `SliceThickness` tags so
the series reconstructs correctly in a PACS viewer. `convert_mha_to_nifti.py` writes one
`.nii.gz` per scan, keeping the same `<patient_id>/<patient_id>_<study_id>_<modality>.nii.gz`
layout as the source `.mha` files. Both converters run one worker process per CPU by default.

`partition_by_center.py` splits the converted NIfTI scans and whole-gland + zonal labels into
one folder per acquiring center, using the `center` column of
`clinical_information/marksheet.csv`:

```shell
sites/<RUMC|PCNN|ZGT>/
├── manifest.csv    # patient_id, study_id for this center
├── nifti/          # symlinks into ../../nifti
├── labels/         # symlinks into ../../labels (whole-gland)
└── zonal_labels/   # symlinks into ../../zonal_labels (PZ/TZ)
```

Each site folder is symlinked back to the shared `nifti/`/`labels/`/`zonal_labels/` files
rather than copied. A study is skipped (and counted) if its scans or either label aren't
present locally yet, e.g. a partial download via `FOLDS`. Point each simulated FL client at
its own `sites/<CENTER>` folder to train on that center's studies only — see
[`../../flower/3d_prostate_segmentation/picai_dataset.py`](../../flower/3d_prostate_segmentation/picai_dataset.py)
for `PicaiSegDataset`, which loads one center's partitioned folder end-to-end.
