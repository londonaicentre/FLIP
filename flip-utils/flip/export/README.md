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

# `flip.export` — packaging a checkpoint as a MONAI Bundle

Turns the checkpoint from a completed FL training run into a **MONAI Bundle**. A MONAI Application
Package (MAP) is built from that bundle: a self-contained container that takes DICOM in, emits DICOM
out, and deploys into a radiology workflow.

Packaging is a **separate step from training**. It runs against an already-uploaded checkpoint, never
inside the federated job, so a packaging failure cannot affect a training run and the FL runtime
carries no deployment dependencies.

**Prerequisite:** PyTorch, which ships in the `full` extra, not the base install:

```bash
pip install "flip-utils[full]"
```

Without it, importing `flip.export` raises `ModuleNotFoundError` with that instruction.

See also:

- Full walkthrough: [`wrap-model-for-deployment.rst`](../../../docs/source/working-with-flip-apps/wrap-model-for-deployment.rst)
- The MAP templates a bundle is packaged into: [`map-apps/`](../../../map-apps/README.md)

## Two bundle forms — a real trade, not a preference

| | `form="torchscript"` (default) | `form="directory"` |
| --- | --- | --- |
| Output | One `.ts` file, `inference.json`/`metadata.json` embedded as TorchScript extra files | A directory: weights under `models/`, configs under `configs/`, the application's own code copied under `scripts/` |
| Needs `torch.jit` | Yes (script or trace) | No |
| MAP packaging today | Both `map-apps/` templates — the form the MONAI Deploy App SDK's `ModelFactory` recognises | **`classification` only** — the SDK's `ModelFactory` does not recognise a directory bundle and hands `MonaiBundleInferenceOperator` a predictor-less placeholder, so `segmentation` fails inside inference; the classification operator loads the bundle itself |
| Cost | The architecture must be scriptable (or traceable with a probe input) | Ships the application source alongside the weights, and rebuilds the architecture at inference time under whatever MONAI the consuming container has — a mismatch breaks the strict `load_state_dict` |

Use `form="directory"` when a model will not script.

- TorchScript is being removed from PyTorch. It has been deprecated since torch 2.5 on every Python, and `torch.jit.script` reports itself unsupported on Python 3.14+.
- `torch.export` is not an alternative. The MONAI Deploy App SDK, through 4.0.0 (the version the MAP templates pin), loads `.ts` and `.pt` files, not an `ExportedProgram`.

Detail: the [packaging guide](../../../docs/source/working-with-flip-apps/wrap-model-for-deployment.rst) and FLIP#1019.

## CLI: `python -m flip.export`

No console script is installed, so invoke it as a module. A top-level `flip` command would be a separate decision
about the package's public surface.

```bash
python -m flip.export \
    --checkpoint FL_global_model.pt \
    --app-dir    app_files \
    --out        bundle/model.ts \
    --input-shape 1,1,96,96,96
```

| Option | Meaning |
| ------ | ------- |
| `--checkpoint PATH` (required) | The aggregated checkpoint, e.g. `FL_global_model.pt`. |
| `--app-dir PATH` (required) | Application directory containing `models.py`. |
| `--out PATH` (required) | Destination — a file for `torchscript`, a directory for `directory`. |
| `--form {torchscript,directory}` | Default `torchscript`. |
| `--inference-config` / `--metadata-config` | Override `<app-dir>/../export/{inference,metadata}.json`. |
| `--method {script,trace}` | Default `script`. Ignored by `--form directory`, which compiles nothing. |
| `--input-shape DIMS` | Comma-separated probe shape, e.g. `1,1,96,96,96`. Required for `--method trace`; with `script` it enables the numerical-equivalence check. |
| `--job-type NAME` | Originating job type, used only to warn if the checkpoint is unlikely to be worth packaging (see `EXPORTABLE_JOB_TYPES` below). |
| `--allow-pickle` | Load the checkpoint with `weights_only=False`. Only for checkpoints of known provenance. |
| `--model-id`, `--project-id`, `--trusts`, `--global-rounds`, `--local-rounds`, `--metric`, `--fl-backend` | Provenance fields recorded into the bundle's `metadata.json` (see below). |

For which MAP template accepts which `--form`, see [Two bundle forms](#two-bundle-forms--a-real-trade-not-a-preference) above.

## Python API

- `export_bundle(checkpoint, app_dir, out, *, ...) -> ExportResult` — the function the CLI wraps.
  It:
  1. reads `inference.json`/`metadata.json` from disk. It **consumes** this bundle configuration and
     never generates it; the app author writes these files once, at authoring time;
  2. loads the checkpoint strictly into the architecture `app_dir/models.py::get_model()` returns;
  3. embeds a `Provenance` block into the metadata;
  4. writes either bundle form.
- `ExportResult` — `output`, `form`, `method` (`None` for `directory`), `max_abs_delta` (vs. eager
  on a probe input; `-1.0` if no `example_input_shape` was given), `num_parameters`,
  `num_state_entries`, `warnings`, and a `.size_bytes` property.
- `EXPORTABLE_JOB_TYPES` — `{"standard", "standard_client_api", "fed_opt"}`; a checkpoint from any
  other job type (e.g. `evaluation`, which produces no new model) only triggers a warning, not a
  refusal.
- `Provenance` (`flip.export.provenance`) — the federated-run record embedded under
  `PROVENANCE_KEY` (`"flip_provenance"`) in the bundle's `metadata.json`. It is embedded rather than
  shipped alongside, so a deployed artefact can never become separated from the record of the run
  that produced it.
  - Fields: `model_id`, `project_id`, `participating_trusts`, `global_rounds`, `local_rounds`,
    `final_aggregate_metric`, `source_checkpoint`, `fl_backend`, `flip_version`, `architecture`,
    plus caller-supplied `extra` fields. Packaging does not call the Central Hub, so every field is
    caller-supplied rather than discovered.
  - `.as_dict()` writes `model_id` and `project_id` under the JSON keys `flip_model_id` and
    `flip_project_id` (the rest keep their field names). It drops empty values, stamps `exported_at`
    and sets `not_for_clinical_use: True`, because a federated research model is neither CE-marked
    nor FDA-cleared.
  - `extra` is applied after the fixed keys, so it can shadow any of them, including
    `not_for_clinical_use`. Nothing in FLIP does this.
  - `.merged_into(metadata)` returns a copy of the author's metadata with the provenance block added.
- Checkpoint reading (`flip.export.checkpoint`): `load_checkpoint(path, allow_pickle=False)` loads a
  `.pt` file; `state_dict_from(data)` and `describe_checkpoint(data, path) -> CheckpointFacts`
  normalise either on-disk shape FLIP produces — FLARE's persistence format (a `model` key plus
  optional `train_conf`/`meta_props`, as `PTFileModelPersistor` writes `FL_global_model.pt` and
  `best_FL_global_model.pt`) or a bare state dict (as carried by user-uploaded evaluation
  checkpoints) — told apart as FLARE's `PTModelPersistenceFormatManager` does (a `model` key), without
  importing NVFLARE, so export needs no FL backend extra (FLIP#1047). `load_app_model(app_dir)`
  instantiates the architecture from `models.py::get_model()`; `load_weights_into_app_model(checkpoint, app_dir, allow_pickle=False)`
  loads the checkpoint's weights into it with `strict=True` and returns the model in eval mode —
  the assumption the whole packaging path rests on, that the aggregated weights and the declared
  architecture still fit one another.

## Where checkpoints come from

`export_bundle` only reads and repackages a checkpoint that FL training already wrote (FLARE's
`PTFileModelPersistor`, or the equivalent Flower staging path) and flip-api already staged or
uploaded to S3. It does not train or produce checkpoints.

## Do not commit exported bundles

Exported bundles are build artefacts. A directory bundle also copies the whole application into
`scripts/`, which no `*.pt`-shaped `.gitignore` rule catches, so `export_bundle` writes a
`.gitignore` (`*`) into the bundle directory itself — see `map-apps/README.md` ("Do not commit
weights") for the rest of the repository-hygiene story.
