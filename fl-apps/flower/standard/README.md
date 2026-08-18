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

# Standard federated training (Flower)

## Overview

The baseline Flower job type (`job_type=standard`): supervised **Federated Averaging** via
[`FlipFedAvg`](../../../flip-utils/flip/flower/strategy.py), which adds the Central Hub telemetry
(status, per-client metrics and exceptions, round events) on top of Flower's `FedAvg`. The template
subclasses it as `FedAvgWithClientMetrics` in [`app/strategy.py`](app/strategy.py) to capture
per-client metrics for the results JSON and to evaluate on the final round only.

## What does the user upload?

The required files (see [`required_files.json`](./required_files.json)) are:

- `client_app.py` — the Flower `ClientApp`, defining `@app.train` and `@app.evaluate`.
- `models.py` — defines the model; `models.get_model` is what `server_app.py` instantiates to seed
  the initial global arrays.

`app/server_app.py`, `app/strategy.py` and `pyproject.toml` ship with the template. Base files win
over uploaded ones — [`bundle_flower_application`](../../../flip-api/src/flip_api/fl_services/services/fl_service.py)
skips any uploaded file whose name collides with a base file — so a user cannot replace the server
app, the strategy or the project config.

## Differential privacy: expected in the uploaded `client_app.py`

`client_app.py` is **uploaded, not templated**, so this template cannot wire the privacy filter
itself the way the NVFLARE templates do (theirs is a `task_result_filters` entry in the
FLIP-owned `config_fed_client.json`). An uploaded Flower training app is expected to privatise its
update by registering FLIP's DP mod on its training handler:

```python
from flip.flower.privacy import flip_local_dp_mod

app = ClientApp()

@app.train(mods=[flip_local_dp_mod])          # training only — leave @app.evaluate alone
def train(msg: Message, context: Context) -> Message:
    ...
```

The mod clips the local update to `dp-clipping-norm` and adds Gaussian noise calibrated to
(`dp-epsilon`, `dp-delta`) before the reply leaves the SuperNode, and is toggled by `dp-enabled` in
`[tool.flwr.app.config]`. See [`flip.flower.privacy`](../../../flip-utils/flip/flower/privacy.py)
for the mechanism and the shipped tutorials for worked examples:
[`xray_classification`](../../../fl-tutorials/flower/xray_classification/README.md) and
[`3d_spleen_segmentation`](../../../fl-tutorials/flower/3d_spleen_segmentation/README.md).

> ⚠️ **This is a convention enforced by code review before upload, not by the platform.** An
> uploaded `client_app.py` that omits the mod will train and aggregate perfectly happily while
> sharing raw model updates. Making it un-bypassable needs a FLIP-owned ClientApp shim that
> `pyproject.toml`'s `clientapp = …` points at — base files win over uploads, so such a shim could
> not be overridden. That is not built yet.

## Run it

The chest-X-ray and 3D spleen-segmentation Flower tutorials both use this job type:

```bash
make -C fl-tutorials download-xray-data FL_BACKEND=flower
make -C fl-tutorials run-tutorial TUTORIAL=xray_classification FL_BACKEND=flower
```

See [`fl-tutorials/flower/`](../../../fl-tutorials/flower/) for the available tutorials and their
datasets.
