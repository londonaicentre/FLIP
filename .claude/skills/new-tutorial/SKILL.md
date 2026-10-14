---
name: new-tutorial
description: Create, adapt, audit and maintain FLIP federated-learning tutorials under fl-tutorials/ (NVFLARE, Flower, or both) — scaffold one from a centralised training script, port one to the other backend or another dataset, check an existing one against the tutorial contract and README shape (figure, reference curve, citations, demo video), or work out which tutorials a flip-utils / fl-apps / guard change touches. Use this whenever the user wants to add, port, clone, review, fix up or "make a tutorial for" a model, dataset or use case (segmentation, classification, EHR/tabular, evaluation, diffusion), asks what files a tutorial needs or what its README should show, or asks why a tutorial is missing from list-tutorials, run-tutorial, the simulator or the demo recorder — even if they don't say "tutorial" but describe an example app for fl-tutorials.
---

# New FLIP tutorial

A tutorial is a complete, runnable example app for one FL backend: the exact files a user would
upload through FLIP, a cohort query, a way to run it on that backend's **simulator** without
containers, and a README. Most of the contract is enforced by CPU-only guards in
`fl-tutorials/tests/`, and the rest is a handful of hand-maintained lists that silently leave a
tutorial out when forgotten. This skill is the map of both.

Read the backend reference before touching files:
- `references/nvflare-tutorial.md` — `app_files/` + `job.py` + `.env.app`, recipe API, job types
- `references/flower-tutorial.md` — `app/` package, the byte-identical template files, run-config keys
- `references/datasets-and-registration.md` — dev data layout, adding a dataset, and the full
  checklist of lists to update (docs, Makefiles, sync script, e2e targets, demo-video profile)

## 1. Pin the scope first

Settle these with the user (or state your assumption) before copying anything, because each
changes the file set:

1. **Backend(s).** New tutorials usually ship on both; a single-backend tutorial is fine when
   the user says so (prostate is Flower-only). Use the same `<name>` on both backends.
2. **Job type.** `standard` (FedAvg training), `evaluation` (no training, uploaded checkpoint),
   `fed_opt`, `diffusion_model` (NVFLARE only). It fixes the required files and the recipe/template.
3. **Modality.** Imaging (cohort must project `accession_id`; images via
   `flip.get_by_accession_number`) or tabular/OMOP-only (`has_imaging=false` project, CPU is enough).
4. **Dataset.** Reuse an existing one under `fl-tutorials/datasets/` when the cohort fits. A new
   dataset is a separate deliverable; a new dataset that must also exist **on the platform** (OMOP
   tables, seeding, published data version) is a large one — say so explicitly rather than
   folding it in silently.
5. **Platform wiring.** Should `make e2e_smoke_<name>` exist? Only possible when the cohort is
   seeded on the dev trusts.

## 2. Build it by copying the nearest tutorial

Never write a tutorial from a blank file. Each reference names the closest existing tutorial per
shape; copy that directory, rename, then edit. Keep what is structural (Makefile, `job.py`,
`pyproject.toml`, the template files, the README skeleton) and replace what is the use case
(model, transforms, cohort, hyperparameters, prose). Preserve the comments in the copied
structural files — they explain traps that have each bitten once (the `.env.app` export line,
`$(abspath …)` on data paths, the undeclared `flip-utils` dependency, no `[tool.flwr.federations]`).

Rules the guards enforce, so get them right the first time:

- **No run-time downloads in app code** (`pretrained=True`, torchvision `weights=`, `torch.hub`,
  `from_pretrained`, MONAI bundle download, `PerceptualLoss`/`LPIPS` without a staged hub dir).
  Weights ship as uploaded files; a tutorial that needs one adds a `make weights` /
  `download-checkpoints` target that fetches it host-side into the tutorial dir.
- **Every `torch.load` passes `weights_only=True`.** Convert upstream pickles host-side under a
  `process_tools/` dir, outside `app/` / `app_files/`.
- **Data only through `flip.FLIP`**; branch on `FlipConstants.LOCAL_DEV` for simulator paths, and
  keep simulator-only knobs inside that branch.
- **Flower: `server_app.py` and `strategy.py` are the template's, byte for byte.** `cp` from
  `fl-apps/flower/<job_type>/app/`.
- **`query.sql`**: single `SELECT` over the `omop` schema, project `person_id` (and `accession_id`
  for imaging), deterministic `ORDER BY`, literal `LIMIT`. Byte-identical across the two backends.
- **Apache 2.0 header** on every source file (Flower-derived files keep the Flower Labs line
  first); the `headers` project skill adds missing ones. Match the neighbours' year.

## 3. Register it

Work through the table in `references/datasets-and-registration.md`. The two that break a run
when forgotten: the `case` entry in `fl-tutorials/flower/sim-tutorial.sh` (Flower), and any new
dataset target appended to `DATASET_TARGETS` in `fl-tutorials/Makefile`. The two that make the
tutorial invisible when forgotten: the NVFLARE list in
`docs/source/components/component-fl-nets.rst` and the path tables in `fl-tutorials/AGENTS.md`
and the root `AGENTS.md`.

## 4. Verify

Run the bundled check first; it runs the guard suite, the sync script, the harness lookups and
(NVFLARE) a GPU-free job export for one tutorial:

```bash
bash .claude/skills/new-tutorial/scripts/check_tutorial.sh <name> [nvflare|flower|both]
```

Then the full CPU suite, which CI runs on every PR touching `fl-tutorials/**`:

```bash
make -C fl-tutorials test
```

Finally the simulator itself, which CI cannot run (GPU for imaging; dataset downloaded first):

```bash
make -C fl-tutorials download-<dataset>-data
make -C fl-tutorials run-tutorial TUTORIAL=<name>                    # NVFLARE
make -C fl-tutorials run-tutorial TUTORIAL=<name> FL_BACKEND=flower  # Flower
```

Report honestly which of the three ran. The simulator run is the only thing that proves the
tutorial trains; if there is no GPU on this host, say so and leave it to the user, pointing at the
pre-release checklist in `CONTRIBUTING.md` (both backends' `run-all-tutorials` are the release gate).

## 5. Document

The tutorial's README follows the shape in its backend reference, and must state: what to upload
(`app_files/` or `app/`), the job type, whether the project has imaging on or off, the dataset
download target, and the simulator command. Every `make -C fl-tutorials <target>` it quotes must
resolve (tested). Update the catalogue lines listed in the registration reference, and mention a
new tutorial in `fl-tutorials/README.md`'s backend table.

**Show what the app does before explaining how.** A reader should get the task from a picture
near the top of the README: what goes in, what comes out. Concretely:

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
  diffusion, evaluation-only, fine-tuning a frozen backbone): a Mermaid block renders on GitHub
  without an asset; the generic job-type figure already lives in the docs
  (`docs/source/assets/fl/job_types.jpg`), so do not redraw that.
- Link the **demo video** (step 6) once it has a published home.

Place the figure in the "Data" / "The use case" section, with a one-line caption that states the
task in plain words.

**Show what a normal run looks like.** A reader's first simulator run ends with numbers and no way
to judge them, so the README's "Expected performance" section carries both a reference curve and
the numbers behind it:

- A **reference training curve** from a simulator run with the shipped config: the best-model
  metric (`BEST_MODEL_METRIC` / `best-model-metric`) and the loss per global round, one line per
  simulated site plus the aggregated global model. Build it from what the run already writes
  rather than new instrumentation: on NVFLARE the trainer's `SummaryWriter` leaves TensorBoard
  event files in the simulator workspace (`/tmp/nvflare/<short>/` by default); on Flower the
  template `server_app.py` writes the cross-validation results JSON (per-round aggregated and
  per-client metrics) under `WORKING_DIR`. Same rules as the data figure: a committed PNG under
  `docs/`, a committed `docs/make_training_curve.py` that reads those artefacts, under 1000 KB.
  No tutorial has one yet; the first one written should be generic enough to move to
  `fl-tutorials/datasets/` or `scripts/` and be shared.
- The **numbers in prose**: the metric range the shipped config reaches, after how many rounds it
  plateaus, which config knobs that depends on (the EHR README's "a narrower layer or fewer rounds
  plateaus lower" is the model), and the known **failure signatures** a reader might mistake for
  normal (the x-ray README's "class imbalance can produce NaN metrics; use ~300 samples per site"
  is one; a metric stuck at round 0's value is another).
- State the hardware and wall-clock the reference run took, so a reader knows whether to wait.

**Cite the work the tutorial ports.** A tutorial built from a published centralised method, a
public dataset or a released checkpoint ends with a `## References` section: the method paper
(authors, venue, year, DOI or arXiv link), the dataset (paper, licence, access URL — the dataset
READMEs already name these in prose), and the checkpoint's source and licence. Give a BibTeX
block for the primary method so a reader can cite it, and point at the repo's `CITATION.cff` for
citing FLIP itself. Say explicitly what differs from the paper (cohort, rounds, model size,
metrics), so the reference numbers above are not read as a reproduction claim. When the tutorial
ports a collaborator's own published method, ask them for the reference rather than guessing one;
the prostate tutorial (nnU-Net-planned zonal segmentation on PI-CAI, ported from a standalone
trainer) is the shape this is written for: cite the method paper, nnU-Net and PI-CAI, and show a
T2W slice with the gland and zonal masks overlaid.

## 6. Record the demo video

Each tutorial gets a walkthrough video recorded against the live dev stack by
`make demo-video` (`flip-api/tests/demo_video.py` orchestrates six Cypress segments from
`flip-ui/test/cypress/demo/` and assembles them with ffmpeg). The recorder is **app-profiled**:
`--app` choices come from the `APPS` dict in `demo_video.py`, so a tutorial becomes recordable by
adding a profile there — `project_name`, `model_name`, `model_description`, `has_imaging: False`
for tabular cohorts (skips the XNAT/OHIF segment), and per-backend `app_dir` / `query_file`
pointing at the tutorial. `flip-api/tests/unit/test_demo_video_helpers.py` parametrises over
`APPS`, so the new profile's paths are checked by `make -C flip-api unit_test`.

```bash
make demo-video DEMO_ARGS="--app <name>"                      # NVFLARE, ~30–45 min, needs the dev stack up + cohort seeded
make demo-video DEMO_ARGS="--app <name> --fl-backend flower"
```

Segmentation tutorials add the same `--data-enrichment-cwd/-cmd` pair the e2e smoke uses, plus
`--publish-segmentations` for the DICOM-SEG overlay beat (copy the spleen invocation in
`flip-ui/test/cypress/demo/README.md`). The segments assume a *training* job (create model →
initiate training → live metrics → download); an `evaluation` tutorial needs a look at segments
4–5 before it can be recorded faithfully — say so rather than record a misleading video. Output
lands in the gitignored `flip-ui/test/cypress/demo/out/flip-demo-<app>.mp4`; it is not committed.
Where it is published (and how the README links it) is decided per the repo's video policy — ask
if none is set yet; the docs GIFs precedent is a versioned HF dataset (`aicentreflip/docs-gifs`, #1236).

## Adapting an existing tutorial

Porting to the other backend: the file sets differ (see both references) but the cohort query,
model factory, transforms/feature engineering, figures and README substance are shared. Copy the
shared files byte-for-byte, add each pair to `scripts/check_tutorial_sync.sh` (NVFLARE copy as the
reference) and widen the sync workflow's `paths:` if needed. Changing the dataset or job type:
re-run §1 for the parts that change (required files, recipe/template, `has_imaging`), then every
registration in §3 that named the old dataset or job type (the Flower `case` entry, `APPS`,
`DICOM_APPS`, the e2e target, the docs catalogue line's job type).

## Auditing an existing tutorial

Run `scripts/check_tutorial.sh <name> both`, then compare the directory against the file table in
its backend reference, the registration checklist, and the README shape in §5 (task figure,
Expected performance with a reference curve, References, video link). Common drift: a Flower
client reading `SUPERNODE_NAME` directly instead of `client_identity(context)` (every simulated
site gets the whole cohort), a README quoting a target the root Makefile does not forward, a
tutorial missing from the docs catalogue or the demo recorder's `APPS`, a `torch.load` without
`weights_only=True`, a README with no figure or citation. Fix what is small in place; file the rest.

## Maintaining the catalogue

When something the tutorials depend on changes, this is what moves with it:

| Change | Touches |
|--------|---------|
| flip-utils API (`flip.flower.identity`, the NVFLARE recipes, `FlipConstants`, `flip.FLIP`) | every tutorial importing it; `make -C fl-tutorials test` runs in flip-utils' env so the CPU suite sees it |
| an `fl-apps/flower/<job_type>/app/*.py` template | re-copy into every Flower tutorial of that job type (the parity test fails until done) |
| an `fl-apps/nvflare/<template>/required_files.json` | every NVFLARE tutorial of that job type must still carry the set |
| a new guard in `fl-tutorials/tests/` | the whole catalogue at once; run `check_tutorial.sh` over every tutorial |
| UI change (`flip-ui/src/**`) | demo videos go stale, curves and figures do not |
| dataset version bump (`trust/.data_version`) or dataset tooling change | reference numbers and figures may change; re-run the curve/figure scripts |
| a release | pre-release checklist: `run-all-tutorials` on both backends, `check_tutorial.sh` over the catalogue, re-record what the table above invalidated |
