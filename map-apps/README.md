<!--
Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at
    http://www.apache.org/licenses/LICENSE-2.0
-->

# MAP application templates

**What happens after a model finishes training on FLIP.**

A completed FLIP run produces a PyTorch checkpoint — a research result, not something a hospital
can use. Radiology departments run services that receive DICOM studies from a PACS, produce a
result, and return it where a radiologist will see it. These templates cover the step in between:
packaging a FLIP-trained model as a **MONAI Application Package (MAP)**, an OCI container that
takes DICOM in and emits DICOM out, ready to be deployed for inference in a radiology suite.

The MAP is becoming an industry standard for clinical AI deployment, used by
[deepc](https://deepc.ai/insight/deepc-establishes-monai-compatibility-strengthening-its-commitment-to-open-source-collaboration-and-global-healthcare-transformation)
and [Siemens Healthineers](https://project-monai.github.io/successstories.html) among others, so
packaging this way keeps a FLIP model portable rather than tied to one vendor.

> **Status: design spike.** The path below has been verified end to end on a development
> workstation. It produces a MAP that runs standalone — it is **not** a deployment procedure for
> any specific platform, and the vendor-supported dependency versions differ from those used here.
> See [`docs/source/working-with-flip-apps/wrap-model-for-deployment.rst`](../docs/source/working-with-flip-apps/wrap-model-for-deployment.rst)
> for the full guide and what remains open, and
> [`map-toolchain-reference.rst`](../docs/source/working-with-flip-apps/map-toolchain-reference.rst) for the pinned
> toolchain and the environment traps.

## Why these live here and not under each tutorial

A MAP consumes trained **weights**, not federated-learning configuration, so it is *not*
backend-specific — the same spleen MAP serves both `fl-tutorials/nvflare/image_segmentation/3d_spleen_segmentation`
and `fl-tutorials/flower/image_segmentation/3d_spleen_segmentation`. Variation falls along two independent axes:

| Varies by **output type** — lives here | Varies by **model** — lives with the app |
| --- | --- |
| operator graph, custom operators, `app.yaml` | preprocessing transforms, spatial shape, label semantics, series-selection rule |

So the reusable pipeline is central, and the per-model bundle configuration sits next to the
training code whose transforms it must mirror, in `<tutorial>/export/`. This mirrors the existing
split between [`fl-apps/`](../fl-apps) (job-type templates) and
[`fl-tutorials/`](../fl-tutorials) (worked examples).

## Layout

```
map-apps/
├── segmentation/          DICOM SEG output — bundle-driven inference
├── classification/        DICOM SR output  — custom operator + SR writer
└── tests/                 CPU-only tests over the custom operator (run in CI: test_map_apps.yml)
```

## Choosing a template

| Model produces | Template | Output object | Inference operator |
| --- | --- | --- | --- |
| a mask | `segmentation/` | DICOM SEG | `MonaiBundleInferenceOperator` |
| labels or scores | `classification/` | DICOM SR (Basic Text) | bespoke — see `classifier_operator.py` |

Classification needs a bespoke operator because `MonaiBundleInferenceOperator` maps an image to an
image; it has no way to emit a label.

## Building a MAP

Both templates are packaged the same way.

**Before you start:**

- `holoscan-cli` **4.2.0**. Version 4.3.0 removed MAP packaging altogether.
- NVIDIA driver **≥ 580**, which the CUDA 13 base image needs. Check with `nvidia-smi`; see [The driver floor](#the-driver-floor).

The UID flags are **required**, not optional. The [packaging guide](../docs/source/working-with-flip-apps/wrap-model-for-deployment.rst) explains why.

```bash
holoscan package map-apps/segmentation \
    --config   map-apps/segmentation/app.yaml \
    --models   <bundle-dir>/model.ts \
    --tag      my_flip_map:latest \
    --platform x86_64 \
    --cuda     13 \
    --sdk      monai-deploy \
    --uid $(id -u) --gid $(id -g)

holoscan run my_flip_map-x64-workstation-dgpu-linux-amd64:latest -i <dicom-dir> -o ./output
```

**Which model file to pass to `--models`:**

- `model.ts` (TorchScript) works for both templates.
- A directory bundle (`flip.export --form directory`) needs no `torch.jit`, but only `classification/`
  can consume one in a packaged MAP. The SDK's `ModelFactory` does not recognise a directory bundle and
  still hands `MonaiBundleInferenceOperator` a placeholder with no predictor, so `segmentation/` fails
  inside inference.

Verified on an RTX 5090. The packaging guide has the detail, and FLIP#1019 the background.

### Python here is 3.12, and stays 3.12

**A MAP does not share an interpreter with FLIP.** `requirements.txt` is installed *inside* the
container the packager builds, on a base image that pins Python 3.12.3. Nothing here is affected by
what the rest of the repo runs on, and nothing here should be bumped in step with it.

That matters because this corner is capped and the rest of the repo is not:

| | Python | why |
| --- | --- | --- |
| `map-apps/` | **3.12, fixed** | `monai-deploy-app-sdk` declares `>=3.10,<3.14`; the holoscan CLI pins Ubuntu 24.04 package versions, so the base image must match |
| everything else | `>=3.12,<3.14` | a deliberate cap (FLIP#1018), not a hard limit — every Python service's unit suite passes on 3.14 |

So when FLIP lifts its own cap to allow 3.14 or later, **leave these templates alone**. The cap is the App SDK's,
it is not ours to lift, and it costs nothing: the MAP is a self-contained container. Bumping it
"for consistency" would break packaging for no benefit.

The pins in `requirements.txt` are load-bearing for a different reason. See the comments there and
the prerequisites table in the packaging guide. In short:

- `monai>=1.6.0`:
  - four high-severity advisories are patched only there;
  - it matches what FLIP trains with;
  - the inherited `<=1.5.0` capped `torch<2.7`, which has no `sm_120` and so cannot use a Blackwell GPU.
- `torch==2.13.0`, from PyPI's default cu130 line.
- `holoscan-cu13` 4.2.0 with `monai-deploy-app-sdk` 4.0.0.

Together these form one coherent CUDA-13 generation, at the cost of the driver floor below.

### The driver floor

The base image is **CUDA 13 / Ubuntu 24.04** (`nvcr.io/nvidia/cuda:13.0.0-runtime-ubuntu24.04`),
so packaging and running both need **NVIDIA driver ≥ 580**. Check `nvidia-smi` first. The Ubuntu
24.04 base is required too, for the reason given in the Python table above.

The manifest naming that image is fetched from GitHub, where only `holoscan-cli` 4.2.0's is
published. That is the second reason for the CLI pin, and why this directory keeps no local
`--source` file.

## Verifying the output

A MAP that exits cleanly has proved nothing about correctness. Check the artefact:

- **DICOM SEG** — confirm `Modality`, segment coding, that `ReferencedSeriesSequence` names the
  input series, and that the frame of reference matches. Then view it overlaid on the source study
  (a standalone Orthanc with the OHIF plugin is enough).
- **DICOM SR** — read `ContentSequence` back. Keep report text ASCII: the writer does not set
  `SpecificCharacterSet`, so non-ASCII characters are stored as `?`.

## Do not commit weights

Exported bundles are build artefacts. `*.ts` cannot be globally ignored because `flip-ui` is
TypeScript, so `.gitignore` carries targeted rules for the paths where TorchScript models appear,
alongside the global `*.pt` rule that covers a directory bundle's weights.

A directory bundle also copies your whole application into `scripts/`, which no weights rule
catches — so `flip.export` writes a `.gitignore` into the bundle itself. That covers any `--out`
you choose, and any repository you export into, which a rule here could not.
