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

> **Two supported paths.** `make sim-tutorial` runs the flwr simulator with no containers at
> all — fast, and what you want while iterating on app code. The Docker Compose stack is the
> pre-merge check: it is the only path that exercises the real deployment wiring, and the only
> one where the sites hold genuinely disjoint data. The app code is identical in both.

### Recommended: Docker Compose

From the Flower service directory:

```bash
cd fl-services/flower
make build                # build the fl-base / superlink / supernode images
make up                   # start fl-api, superlink, supernode-1, supernode-2
```

Then submit the run against the `fl-api` control plane:

```bash
make submit APP=3d_spleen_segmentation    # from fl-services/flower/
```

The default stack publishes no host ports; `make submit` execs into the fl-api
container. To POST from the host instead, publish the fl-api port by editing
the compose file and re-running `make up`.

The dev compose stack (`deploy/compose.development.yml` +
`deploy/compose.development.flower.yml`) wires everything correctly:

- `DEV_DATAFRAME`, `DEV_IMAGES_DIR`, `WORKING_DIR`
  are resolved from `.env.development` (read by Docker Compose as the
  `${VAR}` substitutions in each service's `volumes:` block) and bind-mounted
  into the SuperNode and SuperLink containers — one source of truth for paths.
- Inside the containers the mounts always land at stable locations
  (`/images`, `/dataframe_file`, `/app/runs`, `/app/model_checkpoints`), and
  the `environment:` blocks point the app at those paths, so relative paths in
  tutorial code resolve consistently regardless of your host layout.
- The SuperLink's `--insecure` mode and health server are set by `command:`,
  not by your shell — nothing to re-export between runs.

### Fast local iteration: the flwr simulator (`make sim-tutorial`)

```bash
make -C fl-tutorials sim-tutorial TUTORIAL=3d_spleen_segmentation FL_BACKEND=flower
```

Runs every ClientApp in-process — **no SuperLink container, no SuperNodes, no fl-api, no
Docker**. Use it while iterating on app code; use the compose stack above before merging, since
it is the only path that exercises the real deployment wiring (TLS, fl-api submit, SuperNode
registration).

**The app code is identical either way.** Site identity comes from `context.node_config`'s
`partition-id`, which the simulator populates and a deployed SuperNode accepts via
`--node-config`; `flip.flower.identity.client_identity` reads that, or `SUPERNODE_NAME` when a
container sets it. Nothing in `app/` knows which runtime it is in.

`flwr run` has three sharp edges, which is why this path is a make target rather than a raw
command — `sim-tutorial.sh` handles all three:

1. **A long-lived SuperLink caches its environment.** Ray workers inherit the SuperLink's env,
   not what you exported on the command line, so a SuperLink left over from an earlier run
   silently ignores your `DEV_DATAFRAME` / `WORKING_DIR` and the run dies with
   `PermissionError: '/app'` at the results-writing step. The script stops stale simulator
   processes first — but **only its own**: a blanket `pkill -f flower-superlink` on a host running
   the FLIP dev stack would also kill `deploy-fl-server-net-*`'s superlink, because container
   processes are visible in the host PID namespace. The script skips anything in a container and
   anything outside this checkout.
2. **The ClientApp's working directory is not your project directory.** `flwr run` installs a
   snapshot under `~/.flwr/apps/...` and runs from there, so relative data paths break. The
   script exports absolute `DEV_IMAGES_DIR` / `DEV_DATAFRAME`.
3. **`WORKING_DIR` defaults to `/app/runs`** — a path inside a SuperNode container that does not
   exist on the host. The script points it at the same host directory the compose path uses.

FLIP's `DevSettings` singleton is pinned at import time, so all of the above must be set *before*
the process starts; the script does that, but it does mean you cannot change them mid-run.

**One honest limitation for the imaging tutorials.** Under the compose stack each SuperNode mounts
its own `net-N` slice of the images tree, so the sites hold genuinely disjoint data. A single
simulator process cannot reproduce that, so both simulated clients read the same
`DEV_IMAGES_DIR`. The mechanics — rounds, aggregation, metrics, best-model selection — are
exercised faithfully; the data partitioning is not. Treat simulator results as a smoke test of the
code, not as a federated experiment.

The first run is slow: `flwr run` builds a per-run environment under `~/.flwr/runtime-envs/` and
installs the app's dependencies (MONAI, torch) into it. Later runs reuse it.

## Best-model selection

This tutorial keeps the **best-scoring global model**, not just the last one — a 30-round run has plenty of
room to peak early and drift afterwards. Two run-config keys drive it, in `app/config.toml` for
platform-submitted runs and `[tool.flwr.app.config]` in `pyproject.toml` for local `flwr run`:

```toml
best-model-metric = "test_dice"
best-model-metric-minimize = false
```

`test_dice` is the aggregated test Dice that `app/client_app.py` already reports from its evaluate pass; the
server weight-averages it across trusts by `num-examples` and keeps the round that scores highest.

What changes when it is set:

- The evaluate phase runs **every round** instead of only the last, so each round's freshly aggregated model
  is actually measured — 30 test-split inference passes per client rather than 1. That is the cost of
  selection here; drop the metric to `""` to go back to final-round-only evaluation.
- The results zip gains `best_FL_global_model.pt` next to `FL_global_model.pt`, in the same format, and
  `cross_val_results.json` gains `best_model` / `best_round` / `best_metric`. Nothing is fabricated: with no
  selection, or if the best-model write fails, the file and those keys are simply absent.
- The key must be one the clients actually report. Name a key nobody emits and the run completes with no
  best model at all, logging a warning per round rather than failing — check the ServerApp log if the
  artefact is missing.

## Differential privacy

Training updates are privatised **on the SuperNode**, before the reply leaves the trust. The
`flip_local_dp_mod` mod from
[`flip.flower.privacy`](../../../flip-utils/flip/flower/privacy.py) clips the local update to a
fixed L2 norm and adds Gaussian noise scaled to the configured budget:

```
sigma = dp-sensitivity * sqrt(2 * ln(1.25 / dp-delta)) / dp-epsilon
```

It is wired in `app/client_app.py` as `@app.train(mods=[flip_local_dp_mod])`, so it covers training
rounds only — `@app.evaluate` is untouched. That mirrors the scope of the NVFLARE apps'
`PercentilePrivacy` result filter, though the mechanism is stronger: NVFLARE sparsifies by
percentile and adds no noise, while this is a real (epsilon, delta) mechanism built on Flower's own
`compute_clip_model_update` / `add_gaussian_noise_inplace`.

| Key                | Default | Meaning |
|--------------------|---------|---------|
| `dp-enabled`       | `true`  | Master switch. `false` makes the mod a pass-through, so DP-on / DP-off runs use an identical app |
| `dp-clipping-norm` | `1.0`   | L2 norm the update is clipped to before noise |
| `dp-sensitivity`   | `1e-4`  | How much one training example can move the update |
| `dp-epsilon`       | `10.0`  | Privacy budget — smaller means more privacy and more noise |
| `dp-delta`         | `1e-5`  | Probability the guarantee fails outright |

Override per run without editing the app:

```bash
flwr run . --run-config "dp-enabled=false"
flwr run . --run-config "dp-epsilon=1.0 dp-clipping-norm=0.5"
```

> ⚠️ **The defaults are demonstration values, chosen utility-first** so this tutorial still
> converges with the mechanism live (they give sigma ≈ 4.8e-5). They are **not** a defensible
> privacy budget. A real one calibrates `dp-sensitivity` to the local dataset — roughly
> `2 * dp-clipping-norm / |D|` for an average-of-examples update — and accounts for composition
> across rounds, which this mod does not do: every round spends the budget again. With only a
> handful of trusts the noise also does not average down the way central DP's does.

Integer entries in the state dict (BatchNorm's `num_batches_tracked` counters) pass through
unprivatised. They are step counts rather than learned parameters, and Flower's clipping scales
each array in place by a float, which numpy refuses to write back into an int array — so the mod
excludes them rather than crashing the client the first time clipping engages.

## Running on a real FLIP project: data enrichment

Everything above runs against **local** data. On a real FLIP project the images come from each Trust's
PACS — and PACS supply images only. A segmentation mask is a 3D volume with nowhere to live in OMOP, so
the labels must be uploaded into each Trust's XNAT before training. That is the platform's **data
enrichment** stage.

(Contrast the chest X-ray classification tutorial, whose labels *are* in OMOP: its cohort query projects
them as dataframe columns and it needs no enrichment. See the Data Enrichment user guide.)

Each label must land in the **same scan's `NIFTI` resource** as its image, named to match — the app
pairs them by filename, substituting `/input_` with `/label_`. Run the upload **after the image pull and
after DICOM-to-NIfTI conversion**.

```bash
make -C fl-tutorials download-spleen-data FL_BACKEND=flower

export XNAT_HOST=https://xnat.trust.example
export XNAT_USER=your-username
export XNAT_PASS=your-password

make -C fl-tutorials upload-spleen-labels FL_BACKEND=flower FLIP_PROJECT_ID=<project-uuid> \
  XNAT_URLS="http://127.0.0.1:8104 http://127.0.0.1:8106" DRY_RUN=1
```

`DRY_RUN=1` reports what would happen without changing anything — do that first, then drop it to upload.
One invocation covers every Trust in `XNAT_URLS` (above, the dev roster: GSTT on 8104, KCH on 8106), which
matters because each Trust's XNAT holds only its own studies and a Trust left without labels fails training.

> **This tutorial's download covers only part of the cohort.** `download-spleen-data FL_BACKEND=flower`
> pulls a fixed 6-case HF snapshot and ignores `NUM_CASES`, while the accession mapping spans 41. Enriching
> from it succeeds but leaves most of the cohort unlabelled, and the command says so. For full coverage use
> the MSD download (`make -C fl-tutorials download-spleen-data NUM_CASES=41`) and point
> `SPLEEN_LABELS_DIR` at it — the labels are backend-agnostic once they are in XNAT.

Enrichment is **backend-agnostic**: the labels live in XNAT, so a project enriched once can be trained by
either backend. The upload script is a single copy in
[`fl-tutorials/datasets/spleen/`](../../datasets/spleen); with `FL_BACKEND=flower` the target points it at
this tutorial's FLIP-format tree — see the
[NVFLARE spleen tutorial's README](../../nvflare/image_segmentation/3d_spleen_segmentation/README.md) for
the full walkthrough and options.

## Data Location

By default, the app reads from the shared gitignored `fl-tutorials/data/` root:

- `data/spleen/sample_get_dataframe_response.csv`
- `data/spleen/accession-resources`
