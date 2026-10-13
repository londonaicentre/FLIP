# Datasets, dev data, and every hand-maintained list a tutorial touches

## Dev data: where the simulator reads from

Both harnesses run under `LOCAL_DEV` and read the shared, gitignored `fl-tutorials/data/`:

- Tabular: one CSV, `fl-tutorials/data/<dataset>/dataframe.csv`, with the columns `query.sql`
  would return on the platform (so the app's code is identical). Per-site splits are optional
  (`site1/`, `site2/` for the NVFLARE `SITE{N}_DATAFRAME` path); otherwise the app slices by
  `person_id` modulo.
- Imaging: `DEV_IMAGES_DIR` laid out the way `imaging-api` serves it — one directory per
  accession under `<dataset>/images/` or `accession-resources/`, containing what
  `flip.get_by_accession_number` would download (DICOM series, or NIfTI pairs `input_*.nii.gz` /
  `label_*.nii.gz` for the XNAT-converted path). Look at the existing dataset that is closest in
  modality and mirror its tree.

## Adding a dataset (`fl-tutorials/datasets/<dataset>/`)

Reuse an existing dataset when the cohort fits. Otherwise the dataset dir is its own uv project
(`pyproject.toml` declaring exactly what its scripts import — the per-dataset test run is what
catches an undeclared dependency), with:

1. A download/derive script (host-side; downloading here is allowed and expected — this tree is
   exempt from the offline-apps guard). Public sources only; large or private sets stay
   re-hosted on HF under `aicentreflip/` or behind a `make download-raw-*` the user runs.
2. Targets in `fl-tutorials/datasets/Makefile` named `download-<dataset>-data` (plus any
   `prepare-*` / `upload-*-labels` step), and **the same names appended to `DATASET_TARGETS` in
   `fl-tutorials/Makefile`** — the root forwards a fixed list, and
   `tests/test_fl_tutorials_make_targets.py` fails for any `make -C fl-tutorials <target>` a README
   quotes that does not resolve.
3. Tests under `fl-tutorials/tests/datasets/<dataset>/test_<script>.py`, and `<dataset>` appended to
   `DATASET_TEST_PROJECTS` in `fl-tutorials/Makefile` so they run in that project's env.
4. A section in `fl-tutorials/datasets/README.md`.

If the cohort must also exist **on the platform** (so `make e2e_smoke` and the dev trusts can run
it), the dataset needs the OMOP chain from `datasets/utils/` (#1092): a metadata table,
`omop_convert_<dataset>.py` building the OMOP tables through the shared contract
(`omop_schemas.py`, `omop_mappings.py`, a surrogate-key block in `omop_ids.py`),
`build-<dataset>-canonical`, `verify-<dataset>-omop-tables` through `verify_omop_tables.py
--project <name>`, and `seed-<dataset> KIT=` for a running dev trust. DICOMs regenerated from
open data are never published (only tables + `dicom_metadata.csv` go to `aicentreflip/trust-data`);
a new published cohort bumps `trust/.data_version`. That chain is a project of its own
(the prostate addition touched trust/omop-db, trust/orthanc, the Helm chart and the compose
seeding) — scope it explicitly with the user before starting, and read
`fl-tutorials/datasets/README.md` "OMOP mock-data generation" first.

## Every list to update (checklist)

Derived from the tree, nothing to edit: tutorial discovery on both backends, the parity /
min-clients / import-path / offline-apps guards, ruff.

Hand-maintained — check each one:

| Where | When |
|-------|------|
| `fl-tutorials/flower/sim-tutorial.sh` `case` block | Every Flower tutorial (mandatory, tested) |
| `fl-tutorials/Makefile` `DATASET_TARGETS`, `DATASET_TEST_PROJECTS` | New dataset target / dataset test project |
| `fl-tutorials/datasets/Makefile` + `datasets/README.md` | New dataset |
| `fl-tutorials/tests/tutorial_apps.py` `DICOM_APPS` | App reads 2-D DICOM through MONAI `LoadImaged` |
| `scripts/check_tutorial_sync.sh` `PAIRS` + `.github/workflows/fl-apps-check-tutorial-sync.yml` `paths:` | Files shared byte-for-byte between tutorials |
| `fl-tutorials/AGENTS.md` path table; `fl-tutorials/README.md` backend table and dataset-download sentence | Every tutorial |
| `AGENTS.md` (root) `fl-tutorials/` line in the repo-structure block | Every tutorial (it enumerates them) |
| `docs/source/components/component-fl-nets.rst` "The NVFLARE tutorials" list (name, link, job type) | Every NVFLARE tutorial |
| `docs/source/working-with-flip-apps/create-flip-app-from-{flare,flower}.rst` worked-example bullets; `package-model-as-map.rst` transforms table | When the tutorial is a better worked example / ships `transforms.py` + `export/metadata.json` |
| `docs/source/components/component-omop-database.rst` "Sample cohort queries" | New `query.sql` worth showing |
| `flip-api/Makefile` `e2e_smoke_<name>` target (+ `flip-api/AGENTS.md`, root `AGENTS.md` smoke section); `flip-api/tests/e2e_smoke.py` `TUTORIALS` label map | When the tutorial should be drivable end to end against the dev stack (needs its cohort seeded on the trusts) |
| `flip-api/tests/demo_video.py` `APPS` profile (+ `flip-ui/test/cypress/demo/README.md` example when enrichment flags are needed) | Every tutorial that gets a demo video (`make demo-video DEMO_ARGS="--app <name>"`); same seeded-cohort prerequisite as e2e |
| `CONTRIBUTING.md` pre-release checklist | Only if the tutorial needs an extra input before `run-all-tutorials` (a checkpoint, `NUM_CASES`, a prepare step) |
| `.secrets.baseline` | If detect-secrets flags a placeholder (e.g. a UUID or URL) in the new files |
