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

# The README every tutorial shares

Backend-specific section lists live in the skill's §3 and §4; §5 says what the figure and the
performance section look like for each shape. This file is what is common.

Every README must state: what to upload (`app_files/` or `app/`), the job type, whether the
project has imaging on or off, the dataset download target, and the simulator command. Every
`make -C fl-tutorials <target>` it quotes must resolve (tested). Update the catalogue lines listed
in `registration-checklist.md`, and mention a new tutorial in `fl-tutorials/README.md`'s backend
table.

## Show what the app does before explaining how

A reader should get the task from a picture near the top of the README: what goes in, what comes
out. Concretely:

- An **input → output figure** built from the tutorial's own dataset: a few representative inputs
  (one per class for classification; a slice with the label mask, or the model's prediction, for
  segmentation; for tabular, the feature set and the label's prevalence) — the Ark+ README's
  `docs/dataset_examples.png` is the precedent. Generate it with a committed script,
  `docs/make_<what>_figure.py` plus a `make <what>-figure` target, run from the downloaded data, so
  the figure can be regenerated and a mislabelled panel fails loudly rather than ships (that
  script validates every panel against the cohort dataframe before drawing). Commit the PNG under
  the tutorial's `docs/`, under 1000 KB (the pre-commit large-file limit); downscale rather than
  split the limit. Say which split the panels come from.
- Where the figure reads pixels through pydicom/nibabel directly rather than the app's transform
  chain, say so in the script docstring and the README: it is a picture of the data, not of what
  the model sees (FLIP#821 — the figure stayed upright while the model trained on transposed
  images). The `fl-tutorials/tests/` transform suite is the evidence for that, not the figure.
- A **flow diagram** of the federated loop for this app when it is not plain FedAvg (two-stage
  diffusion, evaluation-only, fine-tuning a frozen backbone, statistics through FedAvg): a Mermaid
  block renders on GitHub without an asset; the generic job-type figure already lives in the docs
  (`docs/source/assets/fl/job_types.jpg`), so do not redraw that.
- Link the **demo video** (below) once it has a published home.

Place the figure in the "Data" / "The use case" section, with a one-line caption that states the
task in plain words.

## Show what a normal run looks like

A reader's first simulator run ends with numbers and no way to judge them, so the README's
"Expected performance" section carries both a reference and the numbers behind it:

- A **reference training curve** from a simulator run with the shipped config: the best-model
  metric (`BEST_MODEL_METRIC` / `best-model-metric`) and the loss per global round, one line per
  simulated site plus the aggregated global model. Build it from what the run already writes
  rather than new instrumentation: on NVFLARE the trainer's `SummaryWriter` leaves TensorBoard
  event files in the simulator workspace (`/tmp/nvflare/<short>/` by default); on Flower the
  template `server_app.py` writes the cross-validation results JSON (per-round aggregated and
  per-client metrics) under `WORKING_DIR`. Same rules as the data figure: a committed PNG under
  `docs/`, a committed `docs/make_training_curve.py` that reads those artefacts, under 1000 KB.
  No tutorial has one yet; the first one written should be generic enough to move to
  `fl-tutorials/datasets/` or `scripts/` and be shared. A statistical tutorial has no curve: its
  reference is a table of agreement between runs.
- The **numbers in prose**: the metric range the shipped config reaches, after how many rounds it
  plateaus, which config knobs that depends on (the EHR README's "a narrower layer or fewer rounds
  plateaus lower" is the model), and the known **failure signatures** a reader might mistake for
  normal (the x-ray README's "class imbalance can produce NaN metrics; use ~300 samples per site"
  is one; a metric stuck at round 0's value is another).
- State the hardware and wall-clock the reference run took, so a reader knows whether to wait.

## Cite the work the tutorial ports

A tutorial built from a published centralised method, a public dataset or a released checkpoint
ends with a `## References` section: the method paper (authors, venue, year, DOI or arXiv link),
the dataset (paper, licence, access URL — the dataset READMEs already name these in prose), and
the checkpoint's source and licence. Give a BibTeX block for the primary method so a reader can
cite it, and point at the repo's `CITATION.cff` for citing FLIP itself. Say explicitly what differs
from the paper (cohort, rounds, model size, metrics), so the reference numbers above are not read
as a reproduction claim. When the tutorial ports a collaborator's own published method, ask them
for the reference rather than guessing one; the prostate tutorial (nnU-Net-planned zonal
segmentation on PI-CAI, ported from a standalone trainer) is the shape this is written for: cite
the method paper, nnU-Net and PI-CAI, and show a T2W slice with the gland and zonal masks overlaid.

## Record the demo video

Each tutorial gets a walkthrough video recorded against the live dev stack by
`make demo-video` (`flip-api/tests/demo_video.py` orchestrates six Cypress segments from
`flip-ui/test/cypress/demo/` and assembles them with ffmpeg). The recorder is **app-profiled**:
`--app` choices come from the `APPS` dict in `demo_video.py`, so a tutorial becomes recordable by
adding a profile there — `project_name`, `model_name`, `model_description`, `has_imaging: False`
for tabular cohorts (skips the XNAT/OHIF segment), and per-backend `app_dir` / `query_file`
pointing at the tutorial. `flip-api/tests/unit/test_demo_video_helpers.py` parametrises over
`APPS`, so the new profile's paths are checked by `make -C flip-api unit_test`.
`platform.demo_profile` names the profile;  checks it points at this tutorial.

```bash
make demo-video DEMO_ARGS="--app <name>"                      # NVFLARE, ~30–45 min, needs the dev stack up + cohort seeded
make demo-video DEMO_ARGS="--app <name> --fl-backend flower"
```

Segmentation tutorials add the same `--data-enrichment-cwd/-cmd` pair the e2e smoke uses, plus
`--publish-segmentations` for the DICOM-SEG overlay beat (copy the spleen invocation in
`flip-ui/test/cypress/demo/README.md`). The segments assume a *training* job (create model →
initiate training → live metrics → download); an `evaluation` tutorial needs a look at segments
4–5 before it can be recorded faithfully — say so rather than record a misleading video. A tutorial on access-controlled data gets no
video either: a recording would show derived values. Output lands in the gitignored
`flip-ui/test/cypress/demo/out/flip-demo-<app>.mp4`; it is not committed. Where it is published
(and how the README links it) is decided per the repo's video policy — ask if none is set yet; the
docs GIFs precedent is a versioned HF dataset (`aicentreflip/docs-gifs`, #1236).
