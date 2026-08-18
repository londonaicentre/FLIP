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

# FLIP federated-learning tutorials

This directory contains complete example applications for FLIP's NVIDIA FLARE and Flower backends. Use them to learn
the model-file contract, run an application locally, or provide the inputs to the full-platform `make e2e_smoke` flow.

## Choose a backend

| Backend | Examples |
| --- | --- |
| [`nvflare/`](nvflare/) | Chest X-ray classification, spleen segmentation and evaluation, diffusion, and template tests |
| [`flower/`](flower/) | Chest X-ray classification, spleen segmentation and evaluation, NumPy, and SuperGrid examples |

The root Makefile forwards tutorial commands to `FL_BACKEND=nvflare` by default. Select Flower explicitly with
`FL_BACKEND=flower`.

## Run an example

List the examples supported by a backend:

```bash
make -C fl-tutorials list-tutorials
make -C fl-tutorials list-tutorials FL_BACKEND=flower
```

For the NVFLARE X-ray example:

```bash
make build-fl                     # once: the simulator runs on the local flare-fl-base image
make -C fl-tutorials download-xray-data
make -C fl-tutorials run-tutorial TUTORIAL=xray_classification
```

`make build-fl` is a genuine prerequisite for the NVFLARE tutorials, not an optimisation: they run on the locally
built `flare-fl-base` image and fail without it. The spleen examples take `download-spleen-data` in place of
`download-xray-data`.

The simulator requires Docker, and GPU-backed examples require the NVIDIA Container Toolkit. Dataset downloads and
generated runs are kept in gitignored backend data/output directories. Run `make -C fl-tutorials run-all-tutorials`
only when you intentionally want the full, heavyweight suite.

## Plot tutorial metrics in MLflow

Tutorial runs can mirror their metrics to the dev-stack MLflow server (FLIP#745), giving loss/accuracy curves per
simulated client and round. Start the server, then export the tracking URI before running:

```bash
make mlflow                                       # MLflow UI on http://localhost:5000 (loopback only)
export MLFLOW_TRACKING_URI=http://localhost:5000  # the simulator runs on the host, not on the stack network
make -C fl-tutorials run-tutorial TUTORIAL=xray_classification
```

Each run appears as an MLflow experiment (`flip/<project id>`) with one metric series per simulated client. The
mirror is best-effort and off by default: with `MLFLOW_TRACKING_URI` unset the simulator behaves exactly as before.
The same server collects the real dev-stack training runs, so simulator and federated runs are comparable side by
side. See [the MLflow component guide](../docs/source/components/component-mlflow.rst) for the full integration.

## Check the sources without a GPU

The tutorial sources also carry a **CPU-only** check that needs none of the above — no GPU, no dataset, no FL image —
and runs in CI on every PR touching `fl-tutorials/**`:

```bash
make -C fl-tutorials test        # ruff over fl-tutorials/ + the transform-chain suite
make -C fl-tutorials pytest      # the suite only (fl-tutorials/tests/)
make -C fl-tutorials lint        # ruff only
```

It pins what each app's preprocessing chain actually feeds its model against the raw DICOM `PixelData` — see
[`tests/README.md`](tests/README.md) for what it does and does not cover.

For network provisioning and standalone service operation, use the
[`fl-services/nvflare/`](../fl-services/nvflare/README.md) or
[`fl-services/flower/`](../fl-services/flower/README.md) guide. For the files a user uploads through FLIP, see
[Working with FLIP apps](https://londonaicentreflip.readthedocs.io/en/latest/working-with-flip-apps.html).
