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

# FL app templates

This tree holds the **base FL application templates** — the FLIP-owned server/orchestration code
that every uploaded model is bundled with — one subtree per FL backend:

```
fl-apps/
├── nvflare/
│   ├── standard/          # JOB_TYPE=standard        — FedAvg (Client API)
│   ├── evaluation/        # JOB_TYPE=evaluation       — cross-site model evaluation
│   ├── diffusion_model/   # JOB_TYPE=diffusion_model  — two-stage latent diffusion
│   ├── fed_opt/           # JOB_TYPE=fed_opt          — Adaptive Federated Optimization
│   └── required_files.json
├── flower/
│   ├── standard/          # job_type=standard         — FedAvg
│   ├── evaluation/        # job_type=evaluation       — federated evaluation
│   └── required_files.json
└── check_required_files.sh
```

Each `<backend>/<template>/` directory is a **job type**: the server-side orchestration code, plus
a declaration of which files the researcher must upload alongside it. See each template's own
README for what it does and what its `required_files.json` requires:
[`nvflare/standard`](nvflare/standard/README.md), [`nvflare/evaluation`](nvflare/evaluation/README.md),
[`nvflare/diffusion_model`](nvflare/diffusion_model/README.md), [`nvflare/fed_opt`](nvflare/fed_opt/README.md),
[`flower/standard`](flower/standard/README.md), [`flower/evaluation`](flower/evaluation/README.md).

All NVFLARE templates are **Client API** apps (`ClientAPIExecutor`, in-process mode) — the legacy
Executor-based templates are retired, though the pre-rename `*_client_api` job-type names still
resolve as aliases for models created before the rename (`flip_api/fl_services/services/fl_service.py`).

## `required_files.json`: per-template source, per-backend generated manifest

Each template directory carries its own `required_files.json` — a flat JSON array of the filenames
a researcher must upload for that job type (e.g. `["trainer.py", "config.json", "models.py"]`).
**Never hand-edit `fl-apps/<backend>/required_files.json`** (the aggregate, one directory up): it is
generated from the per-template files by [`check_required_files.sh`](check_required_files.sh),
keyed by template name, with sorted keys for a byte-stable diff. Edit a template's own
`required_files.json` instead and regenerate:

```bash
bash fl-apps/check_required_files.sh
```

This is enforced two ways, matching the pattern in `.pre-commit-config.yaml`:

- The `fl-apps-required-files` pre-commit hook runs the script and fails the commit if it changed
  anything — re-stage and commit again, no need to remember to run it by hand.
- The `fl-apps-check-required-files.yml` CI workflow re-runs the same script and `git diff
  --exit-code`s the committed manifests, as the backstop for a commit made without pre-commit
  installed.

There is deliberately **no cross-backend combined file** — each backend owns its own job-type
keyspace, so template names never collide across `nvflare/` and `flower/`.

## How flip-api bundles a template

flip-api reads templates straight from `FL_APP_BASE_DIR` (default `/app/fl-apps`, this tree baked
into the image) — no S3 involvement (FLIP#724). Bundling a template is a **positive allowlist, not
a mirror** (FLIP#1008, `flip_api/fl_services/services/fl_service.py::list_local_base_files`): only
two things ever ship —

- The app folder(s): `app/` on both backends, plus NVFLARE's per-site `app_*/` variants (named in
  `meta.json`'s `deploy_map`). Inside an app folder, dot-prefixed entries, `__pycache__`,
  `*.pyc`/`*.pyo`, `uv.lock`, and symlinks are dropped too.
- The backend's single root file: **`meta.json`** for NVFLARE (the job definition NVFLARE deploys),
  **`pyproject.toml`** for Flower (the FAB definition `[tool.flwr.app.components]` resolves against).

Everything else in the template directory — `recipe.py`, `README.md`, the per-template
`required_files.json`, dev tool caches — is excluded and logged at debug level, never shipped to a
trust. The allowlist fails **closed**: for an operator-provided `FL_APP_BASE_DIR`, an unrecognised
file is silently omitted rather than shipped, so a hotfix to a base template ships by rebuilding
(and, in prod, redeploying) the flip-api image, not by editing files in place at a trust.

## The tutorial-sync guards

Some tutorial files are kept as byte-identical copies of another file, and two guards police them
— one derived from the tree, one hand-listed:

- **Flower tutorials vs. the `fl-apps/flower/` templates.** Every `fl-tutorials/flower/*/app` ships
  its own copy of the platform-owned `app/*.py` (`server_app.py`, `strategy.py`) of the template its
  `config.json` job type selects: `flwr build` excludes symlinks from a FAB, so these have to be
  real copies rather than links, and since the bundler discards an uploaded copy in favour of the
  base file (see above), the tutorial must carry what the platform will actually run.
  [`fl-tutorials/tests/test_flower_platform_parity.py`](../fl-tutorials/tests/test_flower_platform_parity.py)
  **derives** the pairs from the tree — every tutorial app against its template's `app/*.py` — and
  fails if a copy differs (a module that is inert in both trees, such as a docstring-only
  `__init__.py`, may). It runs under `make -C fl-tutorials test` and the `fl-tutorials-tests.yml`
  CI workflow (paths `fl-apps/flower/**`, `fl-tutorials/**`), so a new Flower tutorial is covered
  the moment it exists — there is no list and no path filter to extend. Resync by copying the
  template file over the drifted copy.
- **The Ark+ NVFLARE pair.** The two Ark+ evaluation tutorials share `data_utils.py` and
  `arkplus_flat_models.py` with each other; nothing in the tree says so, which is why this is the
  one pair still hand-listed, in [`scripts/check_tutorial_sync.sh`](../scripts/check_tutorial_sync.sh)
  (`PAIRS`), run by the `fl-apps-check-tutorial-sync.yml` CI workflow on any push/PR touching
  `fl-tutorials/nvflare/image_evaluation/**`. Resync by copying the baseline app's file over the
  multimodel copy. Adding another hand-listed pair means extending that workflow's path filters
  too, or drift on the new path goes uncaught.

`pyproject.toml` files are deliberately **not** paired — the template's `[tool.uv]` tables pin
`flip-utils` to the in-image `/opt/flip-utils` source and torch to the cu128 index, neither of which
apply to a tutorial's workstation venv.

## Where to look next

- [`fl-tutorials/`](../fl-tutorials/) — runnable tutorials built on these templates (per-backend,
  with datasets and Makefile targets).
- [`docs/source/working-with-flip-apps.rst`](../docs/source/working-with-flip-apps.rst) — the guides
  for adapting or building an app to upload to FLIP.
- `flip-api/AGENTS.md` and the root `AGENTS.md` (`FL_APP_BASE_DIR`) — how flip-api discovers,
  validates and bundles these templates at runtime.
