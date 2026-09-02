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
tutorial (Flower). Download + preprocessing only — the `PicaiDataset` class that reads this
partitioned data lives with the tutorial itself, at
[`../../flower/3d_prostate_segmentation/dataset.py`](../../flower/3d_prostate_segmentation/dataset.py),
as does the nnU-Net planning step described [below](#nnu-net-plans).

Dedicated uv project (`pyproject.toml` — SimpleITK, tqdm; `uv.lock` is gitignored), the same
pattern as [`../spleen/`](../spleen/).

## Folder structure

```shell
prostate
├── download_data.py        # Downloads PI-CAI images + whole-gland/zonal labels + clinical marksheet
├── convert_mha_to_dicom.py # Converts .mha scans to a DICOM series per study
├── convert_dicom_to_nifti.py # Converts the DICOM series to .nii.gz with the platform's pinned dcm2niix
├── partition_by_center.py  # Splits nifti/labels by acquiring center (RUMC/PCNN/ZGT)
├── create_prostate_metadata_table.py  # DICOM tree -> source/dicom_metadata.csv (+ marksheet); decides source_trust
├── omop_convert_prostate.py           # source/ -> the prostate_project OMOP tables (the seed pipeline's input)
├── upload_prostate_labels_to_xnat.py  # Data enrichment: both masks into every trust's XNAT
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

**Zonal labels are padded onto the T2W grid at download.** picai_labels publishes the HeviAI23
zonal labels as the model's argmax on PI-CAI's preprocessing grid —
[picai_prep](https://github.com/DIAGNijmegen/picai_prep)'s
`crop_or_pad(physical_size=[81, 192, 192], crop_only=True)`, a centre crop of the T2W at native
spacing. A scan that already fits in 192 × 192 × 81 mm comes out unchanged (233 of the 300 fold-0
studies); a larger acquisition, e.g. PCNN's 350 mm field of view, loses its outer rows, columns or
slices, so its zonal file is smaller than the T2W and every viewer that pairs by dimensions (ITK-SNAP
among them) refuses it. The whole-gland label is on the full T2W grid for every study, so
`download_data.py` pads each zonal label onto its whole-gland sibling's grid. The pad is exact —
the crop sits on the T2W lattice, and the model's own softmax
([Zenodo 7615350](https://zenodo.org/record/7615350)) is zero outside the box, so no voxel moves
and none is invented — and it is what `PicaiDataset`'s loader was already doing on the fly, so
training tensors are unchanged. The published file is kept as `zonal_labels_raw/<patient_id>_<study_id>.nii.gz`;
`zonal_labels/` is what everything downstream reads, including the XNAT enrichment uploader, so
image, whole-gland mask and zonal mask share one set of dimensions on disk, in XNAT and in any viewer.
A zonal label that is *not* a sub-lattice of its sibling (different spacing, off the lattice, or
outside the box) is refused rather than resampled. Re-running is a no-op once the tree is padded.

`convert_mha_to_dicom.py` is adapted from [picai_prep](https://github.com/DIAGNijmegen/picai_prep),
which converts DICOM to `.mha` via SimpleITK; it runs that conversion in reverse, writing one
`.dcm` file per slice with correct `PatientID`, `StudyInstanceUID`, `SeriesInstanceUID`,
`ImagePositionPatient`, `ImageOrientationPatient`, `PixelSpacing`, and `SliceThickness` tags so
the series reconstructs correctly in a PACS viewer. It runs one worker process per CPU by default.

What PI-CAI anonymised away is **synthesised**, as the spleen and cxr sets do: a `PatientName`, a
`PatientBirthDate` on which the patient was exactly the recorded `PatientAge` on the study date, a
`ReferringPhysicianName`, and a `StudyDescription` matching the LOINC procedure the OMOP export records
(`synthetic_identity.py`). A study without those tags is not what a hospital PACS hands over, and the
trusts' imaging-api does not import one. Unlike spleen's generators every value is a pure function of
the PI-CAI ids — the same name, birthday and referrer on every run, on any machine — so the published
DICOM set stays reproducible, as its UIDs already were. PI-CAI's own `PatientID`, `AccessionNumber`,
sex, age and acquisition metadata are untouched. None of it belongs to a real person.

`convert_dicom_to_nifti.py` then produces the simulator's
`<patient_id>/<patient_id>_<study_id>_<modality>.nii.gz` **from those DICOM series**, not from the
`.mha` files — with the platform's own pinned dcm2niix image, read from the same
`trust/xnat/xnat/config/dcm2niix_command.json` the trusts' XNAT registers, so there is no second
pin to drift. Two things follow. The scanner metadata below is written exactly once (into the
DICOM) and read back by dcm2niix into a BIDS `<patient>_<study>_<modality>.json` sidecar, instead
of two parallel converters each carrying a copy of the tag list. And what the simulator trains on
is byte-for-byte what an fl-client receives after image pull — the same dcm2niix, the same flags
(`-z y`; the sidecar's `-b y -ba n` adds a file, not bytes) — including its orientation handling,
which a direct SimpleITK conversion does not reproduce. It needs Docker, runs one container per
series (8 in parallel by default), and skips series whose output already exists.

That orientation handling is a real difference, not a detail. dcm2niix stores every volume with the
DICOM row axis reversed (its `isFlipY` default, which the XNAT registration does not override) and
corrects the affine to match, so its voxel array is the `picai_labels` array mirrored along one axis
while both files describe the same physical volume. Pairing image and label by array index therefore
lands the mask flipped against the image (whole-gland Dice 0.52 against its true position on
`10000_1000000`), and the old direct `.mha` conversion only hid that because SimpleITK writes in the
labels' order. `PicaiDataset` in the tutorial loads the image and both masks through MONAI's
`LoadImaged` + `Orientationd` and resamples the masks onto the image grid by affine, which makes the
platform's files and the simulator's interchangeable —
[`../../tests/test_prostate_dataset_orientation.py`](../../tests/test_prostate_dataset_orientation.py)
pins both storage orders.

The DICOM writer carries through the acquisition metadata PI-CAI leaves in the `.mha` headers —
`Manufacturer`, `ManufacturersModelName`, the real acquisition date, `PatientSex`, `PatientAge` and
`PatientIdentityRemoved`. This is the dataset's **only** per-study record of which scanner acquired a
scan (`marksheet.csv` has no scanner columns), so it is worth keeping: it is what lets you partition
finer than the three `center` values, and without it every converted study is stamped with the date it
happened to be converted. NIfTI has nowhere to put them, which is what the sidecar is for.

`convert_mha_to_dicom.py` additionally writes the acquiring `center` into `ClinicalTrialSiteID`
(0012,0030), read from `clinical_information/marksheet.csv` via `--marksheet`. The center is the one
piece of provenance PI-CAI keeps in the marksheet rather than the `.mha` headers, and it is what a
per-site partition keys on — putting it in the DICOM means the contributing center travels with the
study into PACS and XNAT, so downstream steps read it off the image instead of re-joining the
marksheet. A missing marksheet leaves the tag unset and logs a warning rather than failing the
conversion.

**Not `InstitutionName` (0008,0080).** That tag is defined as the institution where the equipment
producing the images is located, and `center` is a contributing cohort rather than a hospital: per
the challenge's [dataset documentation](https://pi-cai.grand-challenge.org/DATA/), the 1500 public
cases come from **11 sites** across these three centers. PCNN is a regional network (Prostaat Centrum
Noord-Nederland) whose studies here span six scanner models and both vendors, and ZGT is a hospital
group. Only RUMC is a single institution, so `InstitutionName` would be a false claim for two values
out of three. `ClinicalTrialSiteID` belongs to the 0012 research/de-identification group the `.mha` headers
already use (`0012|0062`, Patient Identity Removed) and carries the right meaning: the site that
contributed the case. The values are short codes (`RUMC`, `PCNN`, `ZGT`) used as a partition key, so
the ID form fits better than `ClinicalTrialSiteName` (0012,0031).

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
[`../../flower/3d_prostate_segmentation/dataset.py`](../../flower/3d_prostate_segmentation/dataset.py)
for `PicaiDataset`, which loads one center's partitioned folder end-to-end.

## The `prostate_project` seed dataset (platform path)

The simulator reads `sites/<CENTER>` from disk. The **platform** gets the same studies the way it
gets everything: from a trust's OMOP database and PACS, which the seed pipeline
(`make -C trust seed`, FLIP#1100) loads from the public `aicentreflip/trust-data` dataset. The
scripts here produce `prostate_project`'s share of that dataset — the first cohort published with no
snapshot at all — and the chain, from a fold download, is:

```bash
make -C fl-tutorials download-prostate-data FOLDS="0"        # 300 studies, 5 GB
make -C fl-tutorials convert-prostate-to-dicom               # t2w/adc/hbv only (PROSTATE_MODALITIES)
make -C fl-tutorials create-prostate-metadata-table          # data/prostate/source/{dicom_metadata,marksheet}.csv
make -C fl-tutorials build-prostate-omop-tables              # data/prostate/omop/{prostate_project,trust_1,trust_2}/
make -C fl-tutorials build-prostate-canonical                # data/prostate/canonical/prostate_project/ (+ source/)
make -C fl-tutorials package-prostate-dicom                  # verified both ways -> dist/dicom/prostate_project.tar.gz
make -C trust publish-trust-data VERSION=<tag> OMOP_CSV=... DICOM=... CARD=...   # one commit + one tag
```

**`source_trust` is one contributing center per trust**, decided in
`create_prostate_metadata_table.py` from the `ClinicalTrialSiteID` the DICOM carries: ZGT → trust 1,
PCNN → trust 2, RUMC → trust 3. A federation is one institution per site, so centers are never
merged; the two dev trusts get the two centers closest in size (fold 0: ZGT 76 studies, PCNN 69 — an
all-Siemens site against a mostly-Philips one, so the vendor shift comes for free), and RUMC (155
studies) is written as source 3, which a two-trust stack does not load: those rows and DICOMs wait
for a third trust. Every series of a study and every study of a patient lands on one trust, and an
unknown center is an error, never a silent extra trust.

**What goes into OMOP** (`omop_convert_prostate.py`, on the shared contract in
[`../utils/`](../utils/)): one `person` per patient (male; year of birth from the MRI date minus
the marksheet age), one `visit_occurrence` and `procedure_occurrence` (LOINC *MR Prostate WO
contrast*) per study, one `image_occurrence` **per series** (three per study, sharing the study's
accession `<patient>_<study>`), the DICOM-attribute `image_feature`/`measurement` rows spleen
publishes too, and the marksheet as rows a cohort query can select on — PSA, PSA density and
prostate volume as `measurement`; ISUP grade group, clinically significant cancer (yes/no) and
the highest lesion PI-RADS as `observation`
(`../../flower/3d_prostate_segmentation/query.sql` joins them). `person_id` is PI-CAI's numeric
`patient_id`, clear of every other cohort's ids; surrogate keys use `prostate_project`'s reserved
3,000,000 block. The masks are **not** in OMOP — a segmentation has nowhere to live in a cohort
query — which is what the uploader below is for.

**The published set is fold 0 only** (300 of PI-CAI's 1,500 studies: ZGT 76, PCNN 69, RUMC 155),
deliberately, for the size of the dev seed and the XNAT pull. The chain does not care which folds it
is given: `FOLDS="0 1 2 3 4"` (25 GB download, ~13 GB DICOM set, 350 / 350 / 800 studies) followed
by the same steps and a `publish-trust-data` under a new tag would replace `prostate_project` with
the full cohort in place. Not done yet, by choice.

The reproducible path, as for spleen and cxr, needs neither the download nor the conversion:
`make -C fl-tutorials reproduce-prostate-omop` fetches the two published `source/` tables at the
pinned data-version tag, rebuilds the OMOP tables and diffs them against the published ones
(`verify-prostate-omop-tables`, the shared gate). The run that backed tag `20260902` passed on all
seven tables; its output is in the pull request that published the tag, not in a committed log.

**Seeding and enrichment.** `prostate_project` is published from data-version tag `20260902` on (`20260903` re-cut the DICOM with the synthetic identities; the tables are unchanged), and `trust/.data_version` pins `20260903`, so `make -C trust seed-trusts PROJECTS="prostate_project"` loads each dev trust's slice (OMOP rows and DICOMs alike, by `source_trust`) with no override. A project's cohort query then pulls the studies into XNAT,
and the labels follow as data enrichment: `upload_prostate_labels_to_xnat.py` puts the whole-gland
mask (`label_<image>.nii.gz`) and the zonal mask (`zonal_<image>.nii.gz`) into every scan's NIFTI
resource — the mapping is the identity, since the DICOM accession *is* the `picai_labels` file
stem. `make -C flip-api e2e_smoke_prostate` drives cohort → approval → pull → enrichment against a
running stack and stops there (`--stop-after-enrichment`), because no prostate training app
exists yet.

## nnU-Net plans

Training is configured by a pair of JSON files: a **dataset fingerprint** (per-case voxel
spacing, shape after cropping to the non-zero region, and foreground intensity statistics) and
an **experiment plan** derived from it (target spacing, patch and batch size, normalization
scheme, and the U-Net topology). Both come out of
[`calculate_dataset_fingerprint_segmentation.py`](../../flower/3d_prostate_segmentation/calculate_dataset_fingerprint_segmentation.py),
which lives with the tutorial rather than here because it reads the partitioned data through
`PicaiDataset`:

```bash
cd fl-tutorials/flower/3d_prostate_segmentation
uv sync

# one site
uv run python calculate_dataset_fingerprint_segmentation.py \
  --site-dir ../../data/prostate/sites/RUMC \
  --output-dir configs \
  --modality t2w --num-processes 8 --gpu-memory-GB 8

# or pool several — one fingerprint and one plan over all their studies
uv run python calculate_dataset_fingerprint_segmentation.py \
  --site-dir ../../data/prostate/sites/{ZGT,RUMC,PCNN} \
  --output-dir configs \
  --modality t2w --num-processes 8 --gpu-memory-GB 8
```

`--site-dir` takes one or more site folders; several are concatenated into a single dataset, so
the fingerprint spans every study in them and one plan comes out the other end. Either way this
writes `dataset_fingerprint_segmentation.json` and `nnUNetPlans_segmentation.json` into
`--output-dir`. `--modality` picks which scan to plan against (`t2w` by default, matching what
`PicaiDataset` loads), and `--gpu-memory-GB` is the budget the planner sizes patch and batch
against.

**Generate the plans once, then give every client the same file.** Each center scans at its own
resolution, so planning *per site* yields different architectures. Running the single-site form
once per center over the full cohort (`t2w`, `--gpu-memory-GB 8`) gives:

| site | studies | median spacing (d, h, w) | median shape | patch size |
| ---- | ------- | ------------------------ | ------------ | ---------- |
| ZGT  | 350 | `[3.0, 0.5, 0.5]`   | `[21, 383, 383]`  | `[14, 256, 224]` |
| RUMC | 800 | `[3.6, 0.5, 0.5]`   | `[19, 383, 383]`  | `[12, 192, 192]` |
| PCNN | 350 | `[3.0, 0.34, 0.34]` | `[27, 1024, 672]` | `[10, 352, 224]` |
| all three pooled | 1500 | `[3.0, 0.5, 0.5]` | `[21, 383, 383]` | `[10, 192, 160]` |

ZGT and RUMC land on the same topology despite the different patch sizes, but PCNN — the
highest in-plane resolution of the three — keeps stage 3 anisotropic (`kernel_sizes`
`[1, 3, 3]` where the others have moved to `[3, 3, 3]`, with matching `strides` differences).
That changes the shape of the convolution weights, so a client planned on PCNN cannot have its
updates aggregated with one planned on ZGT or RUMC. Which site is the odd one out is not stable
either — it shifts with how many studies each center contributes — so it is not something to
predict from the center alone.

Pooling all three (the last row) lands on the ZGT/RUMC side of that split — stage 3 isotropic,
`kernel_sizes` `[3, 3, 3]` — with a patch size smaller than any single site's, since the plan has
to fit the pooled statistics into the same `--gpu-memory-GB` budget.

So pass every participating site to `--site-dir` in a single run and distribute the resulting
`nnUNetPlans_segmentation.json` to all of them — the same way a real federation would agree the
model spec centrally before training starts. Note this is a *planning-time* pooling of shape and
intensity statistics only, which a live federation would have to derive some other way (a secure
aggregation round, or a published spec); no imaging leaves its site during training itself.
