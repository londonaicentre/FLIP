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

# SuperGrid × MLflow spike (FLIP#744 × FLIP#745)

The FLIP#744-spike xray classification app, extended so that **metrics and the
trained global model leave the ServerApp via MLflow**. On Flower's hosted
SuperGrid the ServerApp runs in a Flower-managed pod with no artifact download
(`flwr pull` is roadmapped ~Q4 2026); Flower Labs' guidance is to ship
artifacts to an experiment tracker. This app does that against the SageMaker
managed-MLflow App, configured entirely through Flower run-config (a SuperGrid
pod carries no FLIP environment variables).

Differences from `../xray_classification`:

- Pinned to the **spike-proven, PyPI-flip-utils-0.1.8-compatible** shape (no
  `FlipFedAvg`, which exists only in the in-repo flip-utils and cannot resolve
  on a Flower-managed pod — there is no `/opt/flip-utils` there).
- `app/mlflow_logging.py`: run-config-driven MLflow writer with loud
  reachability probes (tracking API **and** direct-to-S3 artifact plane — the
  SageMaker App hands out the artifact location, it does not proxy bytes).
- `FedAvgWithClientMetrics` mirrors per-client and aggregated metrics live to
  MLflow (`<metric>/<site>`, step = server round — the FLIP#745 sink
  convention).
- After training, `final_model.pt` + `cross_val_results.json` are uploaded as
  run artifacts and registered as model `flip-supergrid-<model-id>`.
- `pretrained=False` (SuperGrid's egress proxy 403s the weight download).

## Local simulation rehearsal

```bash
cd fl-tutorials/flower/supergrid_xray_mlflow
uvx flwr run . --run-config 'mlflow-tracking-uri="<uri-or-app-arn>" mlflow-aws-access-key-id="..." mlflow-aws-secret-access-key="..."'
```

Against a plain-HTTP MLflow no AWS keys are needed. Needs the xray mini
dataset: `make -C fl-tutorials/flower download-xray-data`.

## SuperGrid run

See the runbook in FLIP#744 (spike comment of 2026-07-16) for login, SuperNode
registration, and the `fl-services/flower/compose.supergrid.yml` rig. Submit
with the federation named explicitly and the MLflow run-config injected:

```bash
uvx flwr run fl-tutorials/flower/supergrid_xray_mlflow <federation> \
  --run-config 'mlflow-tracking-uri="arn:aws:sagemaker:..." mlflow-aws-access-key-id="..." mlflow-aws-secret-access-key="..."' \
  --stream
```

Watch for the `MLFLOW PROBE` / `MLFLOW S3 ARTIFACT PROBE` lines early in the
ServerApp log: they tell you immediately whether the pod's egress allows the
tracking API and the artifact S3 plane.
