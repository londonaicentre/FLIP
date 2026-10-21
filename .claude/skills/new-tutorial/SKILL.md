---
name: new-tutorial
description: Create, adapt, audit and maintain FLIP federated-learning tutorials under fl-tutorials/ (NVFLARE, Flower, or both) — scaffold one from a centralised training script, port one to the other backend or another dataset, check an existing one against the tutorial contract (fl-tutorials/specs/) and README shape (figure, reference curve, citations, demo video), or work out which tutorials a flip-utils / fl-apps / guard change touches. This is the router — it profiles the tutorial (backend, imaging vs tabular, deep learning vs statistical, training vs evaluation, dataset tier) and loads the matching new-tutorial-* sub-skills. Use it whenever the user wants to add, port, clone, review, fix up or "make a tutorial for" a model, dataset or use case (segmentation, classification, EHR/tabular, causal discovery, evaluation, diffusion), asks what files a tutorial needs or what its README should show, or asks why a tutorial is missing from list-tutorials, run-tutorial, the simulator or the demo recorder — even if they don't say "tutorial" but describe an example app for fl-tutorials.
---

# FLIP tutorial — the router

A tutorial is a complete, runnable example app for one FL backend: the exact files a user would
upload through FLIP, a cohort query, a way to run it on that backend's **simulator** without
containers, and a README. What it must satisfy is written once as **specs**
(`fl-tutorials/specs/*.yaml`: one rule per requirement, an id, MUST or SHOULD, a check),
selected per tutorial by its `tutorial.yaml` **manifest** and run by
`make -C fl-tutorials check-tutorial TUTORIAL=<name>`. The specs are the *what*; this skill and
its sub-skills are the *why* and the traps. This file profiles the tutorial, then loads only the
sub-skills whose category applies — never read them all.

Modes: **create** (§1–§6 in order), **adapt**, **audit**, **maintain** (their sections at the
end; every mode starts with §1, because the profile decides which sub-skills and rules apply).

## 1. Profile the tutorial

Settle these with the user, or state your assumption. Together they are the manifest
(`fl-tutorials/specs/README.md` has the schema — copy the nearest tutorial's `tutorial.yaml`):

1. **Backend(s).** New tutorials usually ship on both (`twin:` names the other copy, same
   `<category>/<name>` under `fl-tutorials/<backend>/`, COMMON-31); single-backend is fine when the user says so (prostate is Flower-only; two-stage
   diffusion is NVFLARE-only).
2. **Data.** `imaging_2d` (DICOM series through MONAI), `imaging_3d` (NIfTI converted by XNAT,
   labels as enrichment) or `tabular` (OMOP-only, `has_imaging` off, CPU).
3. **Model.** `deep_learning` (a torch / MONAI module trained by FedAvg) or `statistical` (no
   trainable parameters; sufficient statistics or votes summed through FedAvg).
4. **Loop.** `standard` FedAvg training, `evaluation` of an uploaded checkpoint, `fine_tune`
   from a shipped checkpoint, `fed_opt`, `two_stage`, or `statistics_fedavg`. It fixes the job
   type, the required files and the recipe / template.
5. **Dataset.** Reuse one under `fl-tutorials/datasets/` when the cohort fits. A new dataset is a
   separate deliverable; one that must also exist **on the platform** (OMOP tables, seeding, a
   published data version) is a large one — say so rather than fold it in. Its **tier** decides
   what the target may do: `public` (a download), `access_controlled` (a converter the licence
   holder runs; nothing derived from the data is committed), `private` (synthetic stand-in).
6. **Platform wiring.** `has_imaging`, an `e2e_target` (only when the cohort is seeded on the dev
   trusts), a `demo_profile` or a waiver with its reason.
7. **Prior work.** Before designing the loop, search for existing work that federates this kind of
   workflow (method + "federated", the task + "federated learning", the acronyms), read the
   mechanism (what each client sends, how the server combines it, which assumptions it adds), build
   on the closest one, cite it in `## References`, and say where the tutorial departs from it.

**Paradigm.** FLIP's plumbing is horizontal FedAvg — a size-weighted mean of client arrays (the
NVFLARE recipes; the Flower template `server_app.py` / `strategy.py`, which are fixed). Nothing in
flip-utils, fl-apps or the templates supports vertical or split learning, PSI or personalised FL.
A method that cannot be expressed as weighted averaging needs a design note before any code; the
statistical tutorials show how far the summation channel stretches.

## 2. Route

Invoke every sub-skill whose row applies, backend first, through the Skill tool. Each one ends by
naming its spec; the checker runs those rules for you.

| Axis | Value | Sub-skill (Skill tool) | Spec |
|------|-------|------------------------|------|
| backend | `nvflare` | `new-tutorial-nvflare` | `backend-nvflare.yaml` |
| backend | `flower` | `new-tutorial-flower` | `backend-flower.yaml` |
| data | `imaging_2d`, `imaging_3d` | `new-tutorial-imaging` | `data-imaging.yaml` |
| data | `tabular` | `new-tutorial-tabular` | `data-tabular.yaml` |
| model | `deep_learning` | `new-tutorial-deep-learning` | `model-deep-learning.yaml` |
| model | `statistical` | `new-tutorial-statistical` | `model-statistical.yaml` |
| loop | `evaluation` | `new-tutorial-evaluation` | `loop-evaluation.yaml` |
| loop | `fine_tune`, `fed_opt`, `two_stage` | `new-tutorial-training-variants` | `loop-training-variants.yaml` |
| dataset | new dataset, or tier `access_controlled` / `private` | `new-tutorial-datasets` | `datasets.yaml` |

`standard` and `statistics_fedavg` add no loop skill (the backend and model skills carry them).
`fl-tutorials/specs/profile.yaml` is this table as data; the guard test keeps them identical.

Worked example — biomarker causal discovery on both backends: `new-tutorial-nvflare`,
`new-tutorial-flower`, `new-tutorial-tabular`, `new-tutorial-statistical`, `new-tutorial-datasets`
(ADNI is access-controlled). A spleen-style 3-D segmentation on Flower only:
`new-tutorial-flower`, `new-tutorial-imaging`, `new-tutorial-deep-learning`.

## 3. Build it by copying the nearest tutorial

Never write a tutorial from a blank file. Each backend sub-skill names the closest existing
tutorial per shape; copy that directory, rename, add the `tutorial.yaml`, then edit. Keep what is
structural (Makefile, `job.py`, `pyproject.toml`, the template files, the README skeleton) and
replace what is the use case (model, transforms, cohort, hyperparameters, prose). Preserve the
comments in the copied structural files — each explains a trap that has bitten once.

Rules every tutorial meets regardless of category (`common.yaml`): data only through the `flip`
package with simulator-only logic behind `FlipConstants.LOCAL_DEV` (COMMON-13, COMMON-14);
`query.sql` a single `SELECT` over the `omop` schema, deterministic `ORDER BY`, literal `LIMIT`,
byte-identical across twins and pinned in `scripts/check_tutorial_sync.sh` (COMMON-06 to
COMMON-16); the Apache 2.0 header on every source file — the `headers` project skill adds missing
ones, Flower-derived files keep the Flower Labs line first (COMMON-11, COMMON-12).

## 4. Register

Work through `references/registration-checklist.md`. The two that break a run when forgotten:
the `case` entry in `fl-tutorials/flower/sim-tutorial.sh` (FLWR-10) and a new dataset target
appended to `DATASET_TARGETS` in `fl-tutorials/Makefile` (DATA-01). The ones that make a tutorial
invisible: the docs catalogue, `fl-tutorials/README.md`, both `AGENTS.md` (COMMON-23 to COMMON-25).

## 5. Verify

```bash
make -C fl-tutorials check-tutorial TUTORIAL=<name>          # the specs: static rules, the guard suites, the sync script, NVFLARE make export
make -C fl-tutorials test                                    # the full CPU suite CI runs on every PR touching fl-tutorials/**
make -C fl-tutorials download-<dataset>-data
make -C fl-tutorials run-tutorial TUTORIAL=<name>                    # NVFLARE simulator
make -C fl-tutorials run-tutorial TUTORIAL=<name> FL_BACKEND=flower  # Flower simulator
```

Read the checker's output as a reviewer: ❌ MUST fails the PR; ⚠️ SHOULD is a gap to fix or
file; ⓘ manual is yours to confirm; ⏭ waived is a documented exception, never add one for a gap.
Report honestly which of the three ran. The simulator is the only thing that proves the tutorial
trains; without a GPU on this host say so and leave it to the user, pointing at the pre-release
checklist in `CONTRIBUTING.md` (`run-all-tutorials` on both backends is the release gate).

## 6. Document and record

`references/readme-shape.md` is the README contract every tutorial shares: what to upload, the
job type, the imaging toggle, the dataset target and the simulator command; the **input → output
figure** from a committed script (COMMON-19); **Expected performance** with a reference curve or
table and failure signatures (COMMON-20); **References** with BibTeX (COMMON-21); and the **demo
video** recorded with `make demo-video` from an `APPS` profile (COMMON-18). The category sub-skills
say what the figure and the performance section look like for their kind of tutorial.

## Adapting an existing tutorial

Start from its `tutorial.yaml`: change the axis values that move, and the routing table tells you
which sub-skills to load for the new shape. Porting to the other backend: the file sets differ but
the cohort query, model factory, transforms / feature engineering, figures and README substance are
shared — copy them byte-for-byte and add each pair to `scripts/check_tutorial_sync.sh` (NVFLARE
copy as the reference), widening the sync workflow's `paths:` if needed. Changing the dataset, job
type or loop: re-run §1 for what changes, then every registration that named the old value (the
Flower `case` entry, `APPS`, `DICOM_APPS`, the e2e target, the docs line). Detail in
`references/maintenance.md`.

## Auditing an existing tutorial

`make -C fl-tutorials check-tutorial TUTORIAL=<name>` first; the manifest tells you which
sub-skills to read for the warnings it prints. Then compare the README against
`references/readme-shape.md`. Known drift patterns and what to do with them:
`references/maintenance.md`. Fix what is small in place; file the rest; never waive a gap.

## Maintaining the catalogue

When flip-utils, an `fl-apps` template, a guard, the UI or a dataset version changes, the table in
`references/maintenance.md` says which tutorial files move with it and what to re-run or
re-record. `make -C fl-tutorials check-all-tutorials` is the catalogue-wide pass; CI runs its
static half on every PR (`fl-tutorials/tests/test_tutorial_specs.py`).
