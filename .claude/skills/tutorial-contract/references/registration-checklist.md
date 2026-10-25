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

# Every hand-maintained list a tutorial touches

Derived from the tree, nothing to edit: tutorial discovery on both backends, the parity /
min-clients / import-path / offline-apps / catalogue guards, ruff. Hand-maintained — check each one:

| Where | When |
|-------|------|
| `fl-tutorials/flower/sim-tutorial.sh` `case` block (+ the `RUN_CONFIG` line for evaluation) | Every Flower tutorial (mandatory, tested) |
| `fl-tutorials/Makefile` `DATASET_TARGETS`, `DATASET_TEST_PROJECTS` | New dataset target / dataset test project |
| `fl-tutorials/datasets/Makefile` + `datasets/README.md` | New dataset |
| `fl-tutorials/tests/tutorial_apps.py` `DICOM_APPS` | App reads 2-D DICOM through MONAI `LoadImaged` |
| `scripts/check_tutorial_sync.sh` `PAIRS` + `.github/workflows/fl-apps-check-tutorial-sync.yml` `paths:` | Files shared byte-for-byte between tutorials; every twin pair's `query.sql` (`tests/test_tutorial_catalogue.py` checks it) |
| `fl-tutorials/AGENTS.md` path table; `fl-tutorials/README.md` backend table and dataset-download sentence | Every tutorial |
| `AGENTS.md` (root) `fl-tutorials/` line in the repo-structure block | Every tutorial (it enumerates them) |
| `docs/source/components/component-fl-nets.rst` "The NVFLARE tutorials" list (name, link, job type) | Every NVFLARE tutorial |
| `docs/source/working-with-flip-apps/create-flip-app-from-{flare,flower}.rst` worked-example bullets; `package-model-as-map.rst` transforms table | When the tutorial is a better worked example / ships `transforms.py` + `export/metadata.json` |
| `docs/source/components/component-omop-database.rst` "Sample cohort queries" | New `query.sql` worth showing |
| `flip-api/Makefile` `e2e_smoke_<name>` target (+ `flip-api/AGENTS.md`, root `AGENTS.md` smoke section); `flip-api/tests/e2e_smoke.py` `TUTORIALS` label map | When the tutorial should be drivable end to end against the dev stack (needs its cohort seeded on the trusts) |
| `flip-api/tests/demo_video.py` `APPS` profile (+ `flip-ui/test/cypress/demo/README.md` example when enrichment flags are needed) | Every tutorial that gets a demo video (`make demo-video DEMO_ARGS="--app <name>"`); same seeded-cohort prerequisite as e2e |
| `CONTRIBUTING.md` pre-release checklist | Only if the tutorial needs an extra input before `run-all-tutorials` (a checkpoint, `NUM_CASES`, a prepare step) |
| `.secrets.baseline` | If detect-secrets flags a placeholder (e.g. a UUID or URL) in the new files |
