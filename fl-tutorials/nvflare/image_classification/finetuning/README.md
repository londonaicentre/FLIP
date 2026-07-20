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

# Ark+ fine-tuning — local-simulator replica of the UK–Thailand production run

This tutorial holds the **exact app files used in the cross-continental FLIP
experiment** (`app_files/`: `trainer.py`, Ark+ modules, `config.json`,
`pretrained_weights.pt`) wired to run on the local NVFLARE simulator via
`FlipFedAvgRecipe` — the same `standard_client_api` job type, the same FLIP
`ScatterAndGather` controller, and the same `PercentilePrivacy` filter
configuration (percentile 95, gamma 2.0) as the production deployment.

Because the controller is identical, the simulator log carries the same
`Round N started./finished.`, `Start/End aggregation.` and
`Contribution … ACCEPTED` events that `scripts/extract_model_metrics.sh`
extracts from production CloudWatch. `scripts/extract_simulator_metrics.sh`
extracts them locally into the **same `rounds.tsv`/`summary.md`/boxplot
artefacts**, enabling a platform-overhead baseline comparison.

## Run

```bash
# 1. Point the simulator at the data (defaults to the mini smoke set; for the
#    paper replication uncomment SITE1_*/SITE2_* in .env.app and point them at
#    the UK and Thai synthetic cohorts respectively).
$EDITOR .env.app

# 2. Run the simulation (GPU required; defaults: 2 clients, 50 rounds — the
#    paper's configuration; local epochs/batch/LR come from app_files/config.json)
make sim                       # or: make sim ROUNDS=3 for a quick smoke run

# 3. Extract the production-equivalent metrics from the simulator logs
make metrics
# ... or with a side-by-side platform comparison:
make metrics COMPARE=model_metrics/<model_id>/rounds.tsv
```

Outputs land under `model_metrics/simulator-finetuning-<timestamp>/`.

## Interpretation caveats

- Both simulated clients share **one host and one GPU** (`num_threads = 2`), so
  round durations reflect GPU contention, not two independent sites.
- No WAN, TLS, task encryption, or trust-side services are involved: the
  platform-minus-simulator round-duration delta bundles network transfer,
  platform overhead, and hardware differences. The aggregation and
  inter-round-gap rows are the directly comparable (host-light) quantities.

## Notes

- `pretrained_weights.pt` (~759 MiB) is staged into every site's `app/custom/`
  by `job.py`; first simulator start-up is correspondingly slow.
- `pretrained_weights/` (the unpacked copy of the checkpoint) is not used by
  the tutorial and can be deleted.
