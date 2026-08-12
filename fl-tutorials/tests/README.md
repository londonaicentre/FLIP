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

# fl-tutorials tests

CPU-only pytest suite over the tutorial apps' transform chains. No GPU, no dataset download, no FL
image, no network — the fixtures are synthetic DICOMs built in-process, and the whole suite runs in
a couple of seconds.

```bash
make -C fl-tutorials test        # ruff over fl-tutorials/ + this suite
make -C fl-tutorials pytest      # this suite only
make -C fl-tutorials lint        # ruff only
```

CI runs the same two commands on every push and pull request touching `fl-tutorials/**`
(`.github/workflows/fl-tutorials-tests.yml`), for both backends.

## Why this exists

A preprocessing defect fed the Ark+ foundation model **sideways** radiographs. It survived three
apps and two attempted fixes (`Rotate90d`, then `Flipd` — neither of which can undo a transpose),
depressing every reported metric without erroring: worst case, Pneumothorax AUROC 0.642 against
0.972 on the same checkpoint and cohort. It was found by hand (FLIP#821, FLIP#812, FLIP#820).

Nothing committed could have caught it. Visual review was structurally incapable of doing so: the
one verification artefact in the tree, `arkplus_fine_tuning/docs/make_dataset_figure.py`, reads
pixels through `pydicom` directly, so the published dataset figure showed perfectly upright
radiographs while the model was being fed the transpose.

## What it runs

The environment is **flip-utils'** (`flip-utils[full]` — monai, pydicom, torch, timm, sklearn),
which is what the FL images give these apps at runtime. There is deliberately no `pyproject.toml`
here: the per-tutorial `uv` environments are the wrong target (`arkplus_fine_tuning/pyproject.toml`
does not even declare `monai`, so that environment cannot import its own `data_utils.py`), and one
shared environment matching the runtime is both cheaper and more faithful.

Every app on the MONAI `LoadImaged` DICOM path is covered, both backends — the six entries in
`DICOM_APPS` (`tutorial_apps.py`). Each app's chain is fetched from the app's own factory, not
reconstructed here, so the test asserts on the shipped code.

| Test | Asserts |
| --- | --- |
| `test_phantom_has_no_dihedral_symmetry` | The fixture is non-square **and** distinguishable from all eight of its dihedral variants. |
| `test_phantom_dicom_round_trips` | Each synthetic encoding decodes back to the phantom. |
| `test_loader_prefix_matches_pixel_data` | The chain up to the first resampling transform is `np.array_equal` to `pydicom`'s `PixelData`. |
| `test_full_chain_orientation_is_identity` | The full chain's output correlates best with the **unrotated, unflipped** reference, by a margin. |
| `test_loader_pins_its_reader` | The chain names `PydicomReader(swap_ij=False)` instead of inheriting a reader. |
| `test_chain_composes` / `test_validation_chain_is_deterministic` | Both chains import, compose, run, and the validation chain is reproducible. |

Three design points are load-bearing, and each is itself asserted rather than assumed:

- **The assertion is on values, never on shape.** `Rotate90d(k=-1)` restores the correct shape and
  is still wrong — upright but mirrored, which swaps the patient's left and right and looks
  entirely correct.
- **The full-chain check correlates rather than compares.** The chain's output is `(3, N, N)`
  float32, resized and ImageNet-normalised; the reference is `(H, W)` uint16. Correlation against
  all eight dihedral variants is invariant to both, and names the defect ("looks like transpose")
  in the failure message.
- **The fixture is asymmetric under all eight dihedral transforms**, not merely non-square. A
  mirror-symmetric phantom passes a `Flipd`; a monotone gradient correlates with its own transpose.

The fixture is synthesised, not committed: ~12 KB against ~640 KB for a downsampled real study, no
provenance or PHI question, and the identical code path — the axis order is a property of the
reader's convention, wholly independent of pixel content. It is parametrised over `MONOCHROME1`,
`MONOCHROME2` and RLE Lossless, where the array path genuinely differs.

## What it does **not** cover

This is a check on axis order and chain composition. It is not coverage of the DICOM loading
contract, and should not be mistaken for it. Untested here:

- `MONOCHROME1` polarity inversion (the fixture is parametrised over it, but the assertions are
  orientation-only — `correlation()` takes an absolute value precisely so polarity does not enter).
- `RescaleSlope` / `RescaleIntercept` application.
- VOI LUT / window-level handling.
- Signed pixel data (`PixelRepresentation = 1`).
- Multi-frame instances.
- Whether "matches `PixelData`" is the same thing as "matches Ark+'s pretraining convention".
- End-to-end training behaviour, which stays with the GPU simulator harness
  (`make -C fl-tutorials run-tutorial`).

**Out of scope by design:** the spleen and latent-diffusion tutorials. They load 3-D NIfTI through
`Orientationd`/`Spacingd`, where this correction would be actively wrong. Do not add them to
`DICOM_APPS`.

Tutorials are copy-and-adapt example code, and a repository test cannot follow a copy out of the
repository. It can keep the thing being copied correct, which is the point: `fl-apps/` (the
templates flip-api actually bundles) contains no image-loading code at all, so `fl-tutorials/` is
the reference implementation everyone starts from.
