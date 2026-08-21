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
> See [`docs/source/working-with-flip-apps/package-model-as-map.rst`](../docs/source/working-with-flip-apps/package-model-as-map.rst)
> for the full guide, the environment traps, and what remains open.

## Why these live here and not under each tutorial

A MAP consumes trained **weights**, not federated-learning configuration, so it is *not*
backend-specific — the same spleen MAP serves both `fl-tutorials/nvflare/…/3d_spleen_segmentation`
and `fl-tutorials/flower/3d_spleen_segmentation`. Variation falls along two independent axes:

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
└── holoscan-source.json   pinned base-image manifest (see below)
```

## Choosing a template

| Model produces | Template | Output object | Inference operator |
| --- | --- | --- | --- |
| a mask | `segmentation/` | DICOM SEG | `MonaiBundleInferenceOperator` |
| labels or scores | `classification/` | DICOM SR (Basic Text) | bespoke — see `classifier_operator.py` |

Classification needs a bespoke operator because `MonaiBundleInferenceOperator` maps an image to an
image; it has no way to emit a label.

## Building a MAP

Both templates are packaged the same way. `holoscan-source.json` and the UID flags are
**required**, not optional — see the guide for why.

```bash
holoscan package map-apps/segmentation \
    --config   map-apps/segmentation/app.yaml \
    --models   <bundle-dir>/model.ts \
    --tag      my_flip_map:latest \
    --platform x86_64 \
    --sdk      monai-deploy \
    --source   map-apps/holoscan-source.json \
    --uid $(id -u) --gid $(id -g)

holoscan run my_flip_map-x64-workstation-dgpu-linux-amd64:latest -i <dicom-dir> -o ./output
```

### Python here is 3.12, and stays 3.12

**A MAP does not share an interpreter with FLIP.** `requirements.txt` is installed *inside* the
container the packager builds, on a base image that pins Python 3.12.3. Nothing here is affected by
what the rest of the repo runs on, and nothing here should be bumped in step with it.

That matters because this corner is capped and the rest of the repo is not:

| | Python | why |
| --- | --- | --- |
| `map-apps/` | **3.12, fixed** | `monai-deploy-app-sdk` declares `>=3.10,<3.14`; the holoscan CLI pins Ubuntu 24.04 package versions, so the base image must match |
| everything else | `>=3.12,<3.14` | a deliberate cap (FLIP#1018), not a hard limit — every Python service's unit suite passes on 3.14 |

So when FLIP eventually moves past 3.14, **leave these templates alone**. The cap is the App SDK's,
it is not ours to lift, and it costs nothing: the MAP is a self-contained container that takes DICOM
in and emits DICOM out. Bumping it "for consistency" would break packaging for no benefit.

The pins in `requirements.txt` are load-bearing for a different reason — see the comments there and
the prerequisites table in the packaging guide. In short: `monai>=1.5.2` (1.5.0 caps `torch<2.7`,
which has no `sm_120` and so cannot use a Blackwell GPU) and `torch==2.11.0+cu128` (with the ceiling
gone, PyPI's default wheel is cu130, which needs NVIDIA driver >= 580).

### `holoscan-source.json`

The packager normally downloads a base-image manifest from GitHub, where most versions now return
404. This file pins the manifest locally and selects a **CUDA 12 / Ubuntu 24.04** base image:
CUDA 13 images require NVIDIA driver ≥580, and Ubuntu 24.04 is required because the CLI pins
Python 3.12.3 package versions.

## Verifying the output

A MAP that exits cleanly has proved nothing about correctness. Check the artefact:

- **DICOM SEG** — confirm `Modality`, segment coding, that `ReferencedSeriesSequence` names the
  input series, and that the frame of reference matches. Then view it overlaid on the source study
  (a standalone Orthanc with the OHIF plugin is enough).
- **DICOM SR** — read `ContentSequence` back. Keep report text ASCII: the writer does not set
  `SpecificCharacterSet`, so non-ASCII characters are stored as `?`.

`--models` takes a `model.ts` file for either template. `flip.export --form directory` writes a
directory bundle instead, which needs no `torch.jit` — but only `classification/` can consume one in
a packaged MAP: the SDK's `ModelFactory` does not recognise a directory bundle and still hands
`MonaiBundleInferenceOperator` a placeholder with no predictor, so `segmentation/` fails inside
inference. Verified on an RTX 5090. The packaging guide has the detail, and FLIP#1019 the background.

## Do not commit weights

Exported bundles are build artefacts. `*.ts` cannot be globally ignored because `flip-ui` is
TypeScript, so `.gitignore` carries targeted rules for the paths where TorchScript models appear,
alongside the global `*.pt` rule that covers a directory bundle's weights.

A directory bundle also copies your whole application into `scripts/`, which no weights rule
catches — so `flip.export` writes a `.gitignore` into the bundle itself. That covers any `--out`
you choose, and any repository you export into, which a rule here could not.
