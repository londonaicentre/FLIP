<!--
    Copyright (c) 2026 Flower Labs GmbH
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

---

tags: [quickstart]
dataset: [spleen]
framework: [monai]
---

# Federated Learning with MONAI and Flower (Quickstart Example)

This example of Flower uses a small MONAI UNet based on FLIP's implementation and a training-only `ClientApp`. It reads NIfTI data from the local `./data` folder and does not write any outputs.

## Set up the project

### Folder structure

```shell
3d_spleen_segmentation
├── app
│   ├── __init__.py
│   ├── client_app.py   # Defines your ClientApp
│   ├── data_loading.py # MONAI transforms + datalist
│   ├── get_data.py     # Placeholder function for FLIP API, reads local data
│   ├── server_app.py   # Defines your ServerApp
│   └── task.py         # Defines model creation
├── pyproject.toml      # Project metadata like dependencies and configs
└── README.md
```

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
make submit APP=3d_spleen_segmentation
```

The default stack publishes no host ports; `make submit` execs into the fl-api
container. Use `make up-debug` if you want to POST from the host
(`curl -X POST http://localhost:8000/submit_tutorial/3d_spleen_segmentation`) instead.

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

## Data Location

By default, the app reads from:

- `data/sample_get_dataframe_response.csv`
- `data/accession-resources`
