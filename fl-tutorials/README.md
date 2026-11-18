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

# FLIP federated-learning tutorials

This directory contains complete example applications for FLIP's NVIDIA FLARE and Flower backends. Use them to learn
the model-file contract, run an application locally, or provide the inputs to the full-platform `make e2e_smoke` flow.

## Choose a backend

| Backend | Examples |
| --- | --- |
| [`nvflare/`](nvflare/) | Chest X-ray classification, Ark+ fine-tuning and the two Ark+ evaluations (baseline and multi-model), spleen segmentation and evaluation, [image generation](nvflare/image_generation/README.md) (brain MRI latent diffusion in two jobs, chest X-ray diffusion), EHR risk prediction (tabular/OMOP-only), biomarker causal discovery (`biomarker_causal_discovery`: federated bootstrapped FCI, tabular/OMOP-only) |
| [`flower/`](flower/) | Chest X-ray classification, spleen segmentation and evaluation, prostate zonal segmentation (PI-CAI, nnU-Net-planned), EHR risk prediction (tabular/OMOP-only), biomarker causal discovery (`biomarker_causal_discovery`: federated bootstrapped FCI, tabular/OMOP-only) |

Both trees are laid out as `fl-tutorials/<backend>/<category>/<name>/` (#1500). A tutorial's two copies sit at the
same relative path. The category names the task family (`image_classification`, `image_segmentation`,
`image_evaluation`, `image_generation`, `tabular_classification`, …), and every `make` target addresses a tutorial by
`<name>` alone.

`fl-tutorials/Makefile` targets FLARE by default (`FL_BACKEND=nvflare`); select Flower with `FL_BACKEND=flower`.

## Run an example

List the examples supported by a backend:

```bash
make -C fl-tutorials list-tutorials
make -C fl-tutorials list-tutorials FL_BACKEND=flower
```

For the chest X-ray example, on either backend:

```bash
make -C fl-tutorials download-xray-data
make -C fl-tutorials run-tutorial TUTORIAL=xray_classification                    # FLARE simulator
make -C fl-tutorials run-tutorial TUTORIAL=xray_classification FL_BACKEND=flower  # Flower simulator
```

`run-tutorial` runs the simulator on both backends in the `flip-utils` environment with its `full` extra (the package set the FL images install), so it needs no FL image. The FL images themselves are exercised by `make e2e_smoke` against the dev stack.

Each example needs its data step first, in place of `download-xray-data` above:

| Example | Backends | Data step (`make -C fl-tutorials …`) |
| --- | --- | --- |
| Chest X-ray classification | both | `download-xray-data` |
| Spleen segmentation and evaluation | both | `download-spleen-data` |
| EHR risk prediction (tabular/OMOP-only, no imaging; CPU is enough) | both | `download-synthea-data` |
| Biomarker causal discovery (tabular/OMOP-only) | both | `convert-adni-cohort ADNI_DIR=<your IDA download>` |
| Ark+ fine-tuning | FLARE only | `download-arkplus-finetuning-data`, plus the raw Ark+ checkpoint |
| Ark+ evaluations (baseline and multi-model) | FLARE only | `download-arkplus-eval-data`, plus the raw Ark+ checkpoint |
| Prostate zonal segmentation | Flower only | `prepare-prostate-local-data NUM_CASES=12` (simulator) |

- **ADNI:** the data is access-controlled and never enters the repository. You apply for ADNI access yourself, and the converter builds the cohort from the tables you download. The tutorial README has the application and download steps.
- **Ark+ checkpoint:** run `make download-raw-checkpoint` in the tutorial directory; see its README.
- **Prostate:** for the full PI-CAI setup and the nnU-Net plan, see its [README](flower/image_segmentation/3d_prostate_segmentation/README.md).

GPU-backed examples need a CUDA GPU on the host.

Dataset tooling lives in [`datasets/`](datasets/) and is shared by both backends. Downloads land in the shared, gitignored `data/` root, so one download serves both. Generated runs stay in gitignored per-backend output directories.

`make -C fl-tutorials run-all-tutorials` runs the full, heavyweight suite, so run it only when you mean to. It is the pre-release gate, run for both backends before a release is published (see [*Pre-release checklist*](../CONTRIBUTING.md#pre-release-checklist)). The record of that run is the *Release checks* section of the release notes.

## Check the sources without a GPU

The tutorial sources also carry a **CPU-only** check that needs none of the above — no GPU, no dataset, no FL image —
and runs in CI on every PR touching `fl-tutorials/**`:

```bash
make -C fl-tutorials test              # ruff over fl-tutorials/ + both suites below
make -C fl-tutorials pytest            # tutorial-app suite only (fl-tutorials/tests/, minus tests/datasets/)
make -C fl-tutorials pytest-datasets   # dataset-tooling suites only (fl-tutorials/tests/datasets/, one env per dataset)
make -C fl-tutorials lint              # ruff only
```

It pins what each app's preprocessing chain actually feeds its model against the raw DICOM `PixelData` — see
[`tests/README.md`](tests/README.md) for what it does and does not cover, and for why the suites run in
different environments.

It does not run the tutorials themselves; real training is covered by `run-all-tutorials` on a GPU host (see [Run an example](#run-an-example)).

## Adding or changing a tutorial

The tutorial contract is written down once, as the `tutorial-contract` project skill in [`.claude/skills/tutorial-contract/`](../.claude/skills/tutorial-contract/SKILL.md), with two references: `readme-shape.md` and `registration-checklist.md`. It reads as plain Markdown without Claude Code. It covers:

- the file set per backend;
- the files that must stay byte-identical to the `fl-apps` templates;
- the hand-maintained lists a tutorial has to be added to;
- the README shape (task figure, reference training curve, references, demo video).

Two commands drive it:

- `/new-tutorial`: shape questions, copy the nearest tutorial, registrations, verify.
- `/audit-tutorial <name>`: guards, GPU-free export, registrations, the README checklist.

The guards stay the source of truth. `make -C fl-tutorials test` runs them, including `tests/test_tutorial_catalogue.py`, which checks that a twin pair's cohort query is pinned in the sync script, that `fl_job/` is ignored, the Flower identity from the context, and the cohort query's shape. Background: [#1444](https://github.com/londonaicentre/FLIP/issues/1444).

For network provisioning and standalone service operation, use the
[`fl-services/nvflare/`](../fl-services/nvflare/README.md) or
[`fl-services/flower/`](../fl-services/flower/README.md) guide. For the files a user uploads through FLIP, see
[Working with FLIP apps](https://londonaicentreflip.readthedocs.io/en/latest/working-with-flip-apps.html).
