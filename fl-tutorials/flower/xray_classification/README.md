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

# Chest-X-ray multi-lesion classification (Flower)

Flower port of the chest-X-ray tutorial that lives at
`flip-fl-base/tutorials/image_classification/xray_classification`. It scores
every X-ray against the lesions named in `app/config.json` (Effusion + Edema
by default, with "Lungs in normal arrangement" as a negative override) using
a MONAI DenseNet121 trained with multi-label BCE.

The cohort query (`query.sql`) is copied verbatim from the NVFLARE tutorial
and matches the chest-X-ray data seeded into the trust mock OMOP DB
(concept ids 4215818 / 4196943 / 40481136). This is the tutorial that
`make e2e_smoke` from FLIP picks when `FL_BACKEND=flower`.

## Folder structure

```
xray_classification/
├── query.sql                  # Cohort SQL — verbatim from flip-fl-base
├── pyproject.toml             # Dependency manifest; not consumed by FLIP (base bundle wins)
├── README.md
└── app/
    ├── __init__.py
    ├── config.json            # Per-tutorial hyperparameters (LOCAL_ROUNDS, splits, LESIONS, ...)
    ├── client_app.py          # @app.train + @app.evaluate
    ├── task.py                # train_func / validate_func helpers
    ├── data_loading.py        # FLIP_BASE + lesion / row helpers
    ├── transforms.py          # MONAI X-ray transforms
    ├── models.py              # DenseNet121
    ├── loss_and_metrics.py    # BCE loss + per-lesion P/R/F1
    ├── server_app.py          # symlink → ../../../src/standard/app/server_app.py
    └── strategy.py            # symlink → ../../../src/standard/app/strategy.py
```

`server_app.py` and `strategy.py` are symlinks to the canonical base bundle in
`src/standard/app/`. They exist only so that `flwr run` from this tutorial
can resolve `app.server_app` / `app.strategy` locally — FLIP's
`bundle_flower_application` overlays the same base files at deploy time, so
the upload flow is unaffected.

## Running this tutorial

> **Run this tutorial with the Docker Compose stack, not `flwr run` / the
> Simulation Engine.** The compose stack is the supported path; the
> simulation path is documented below only so you understand why we avoid it.

### Recommended: Docker Compose

From the repository root:

```bash
make build                # build the fl-base / superlink / supernode images
make up                   # start fl-api, superlink, supernode-1, supernode-2
```

Then submit the run against the `fl-api` control plane:

```bash
curl -X POST http://localhost:8000/submit_run/xray_classification
```

The compose file (`deploy/compose.yml`) wires everything correctly:

- `DEV_DATAFRAME`, `DEV_IMAGES_DIR`, `WORKING_DIR`
  are resolved from `.env.flwr.development` (read by Docker Compose as the
  `${VAR}` substitutions in each service's `volumes:` block) and bind-mounted
  into the SuperNode and SuperLink containers — one source of truth for paths.
- Inside the containers the mounts always land at stable locations
  (`/images`, `/dataframe_file`, `/app/runs`, `/app/model_checkpoints`), and
  the `environment:` blocks point the app at those paths, so relative paths in
  tutorial code resolve consistently regardless of your host layout.
- The SuperLink's `--insecure` mode and health server are set by `command:`,
  not by your shell — nothing to re-export between runs.

The `DEV_DATAFRAME` CSV must expose chest-X-ray accession IDs with the same
column shape `query.sql` returns from the trust mock OMOP DB (concept ids
4215818 / 4196943 / 40481136). DICOMs land in `DEV_IMAGES_DIR` keyed by
accession number — `flip.get_by_accession_number(..., resource_type=[ResourceType.DICOM])`
reads them directly when `LOCAL_DEV=true`.

### Not recommended: Flower Simulation Engine (`flwr run`)

We deliberately do **not** document a `flwr run` invocation for this tutorial.
Running it via the Simulation Engine is technically possible but brittle, for
reasons specific to this project:

1. **Long-lived `flower-superlink` caches its environment.** `flwr run`
   submits jobs to an already-running `flower-superlink` daemon. Ray worker
   subprocesses inherit the superlink's env, *not* the env you exported on the
   `flwr run` command line — so changing `DEV_DATAFRAME=…` between runs has
   no effect until you `pkill -f flower-superlink`.
2. **ClientApp CWD is not your project directory.** `flwr run` installs a
   snapshot of the app under `~/.flwr/apps/<publisher>.<name>.<version>.<hash>/`
   and runs ClientApp subprocesses from there, so relative paths like
   `../../data/...` resolve to `~/.flwr/data/...` and fail.
3. **FLIP's `DevSettings` singleton is pinned at import time.**
   `flip/constants/pt_constants.py` reads `FlipConstants.LOCAL_DEV` at
   class-body time, which forces pydantic-settings to materialise the
   singleton before any run starts. Once pinned, later `os.environ[...]`
   writes don't propagate, so mid-run path overrides are a dead end.

Under Docker Compose none of these bite: each container starts fresh, env
vars are applied from `env_file`/`environment:` at container start, and CWDs
are fixed by `working_dir:`.

If you still want to experiment with `flwr run` locally you will have to
(a) `pkill -f flower-superlink` before every run with new env, (b) use
absolute paths (`$(git rev-parse --show-toplevel)/data/...`), and (c) accept
that some FLIP-side behaviour driven by the import-time singleton will still
reflect whatever env the superlink was born with. Don't do it for real work —
use the compose stack above.

## Hyperparameters

`app/config.json` mirrors the NVFLARE chest-X-ray config:

| Key                  | Default | Meaning |
|----------------------|---------|---------|
| `LOCAL_ROUNDS`       | 3       | Local epochs per global round |
| `LR_START` / `LR_END`| 1e-3 / 1e-4 | ExponentialLR sweep across the local round |
| `VAL_SPLIT`          | 0.2     | Validation fraction |
| `TEST_SPLIT`         | 0.2     | Test fraction (used by `@app.evaluate`) |
| `BATCH_SIZE`         | 8       | DataLoader batch size |
| `LESIONS`            | Effusion / Edema / "Lungs in normal arrangement" | Multi-label heads + the normal-override column |
| `value_to_numerical` | {0:"No",1:"Yes"} | Maps dataframe string values to binary labels |
| `VALIDATE_EVERY`     | 1       | Validate every N epochs (currently always 1) |

## Data assumptions

- `query.sql` resolves chest-X-ray accession IDs labeled with effusion / edema
  / normal lungs from the trust mock OMOP DB.
- DICOM payloads come back via `flip.get_by_accession_number(..., resource_type=[ResourceType.DICOM])`.
- Each `.dcm` becomes one training sample with the row's lesion labels.
