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

# Running the Ark+ Experiments on FLIP

End-to-end runbook for the three Ark+ chest-X-ray experiments on a FLIP deployment
(validated on **AWS staging**: hub-only ECS Fargate + two on-desktop trusts, GSTT + KCH,
against the synthetic DeCaf MICCAI-2026 dataset).

## The three experiments

| # | Experiment | Tutorial dir (`fl-tutorials/nvflare/`) | Job type | Dataset | Cohort SQL discriminator |
|---|---|---|---|---|---|
| 1 | **Baseline evaluation** | `image_evaluation/arkplus_baseline_classification_evaluation` | `evaluation_client_api` | **holdout** | `procedure_source_value = 'Chest X-ray (holdout)'` |
| 2 | **Finetuning** | `image_classification/arkplus_fine_tuning` | `standard_client_api` (training) | **finetuning** (train split) | `procedure_source_value = 'Chest X-ray'` |
| 3 | **Multimodel evaluation** | `image_evaluation/arkplus_multimodel_classification_evaluation` | `evaluation_client_api` | **holdout** | `procedure_source_value = 'Chest X-ray (holdout)'` |

**Dependency:** the multimodel evaluation (3) evaluates the pretrained **and** the finetuned
checkpoint, so run finetuning (2) first and feed its output checkpoint into (3).

Each tutorial's `app_files/` carries the model code + `config.json`; the pretrained Ark+ weights
(`arkplus_pretrained_weights.pt`, ~759 MiB) ship in the baseline/multimodel `app_files/`. The
hub **de-bundles** these large checkpoints server-side (they are staged on the fl-server, never
shipped to clients — see FLIP#695), so the model-file upload cap must allow ~759 MiB
(`MAX_MODEL_FILE_BYTES`, default 5 GiB on the hub). The multimodel evaluation (3) additionally
requires the fl-server's `flip` package to **name its evaluation broadcasts**
(`EvaluationModelLocator` stamping `flip_eval_model_name` on each validate task's DXO meta) — the
evaluator attributes each broadcast's weights by that key and fails with a clear error on older
fl-server images.

> **⚠️ The finetuning checkpoint is different from the eval checkpoints — it must be _backbone-only_.**
> The **eval** experiments (1, 3) score the *full* foundation model, so their `pretrained_weights.pt`
> keeps the original 14-class `omni_heads.*`. **Finetuning (2)** loads only the Swin backbone and trains
> a **fresh 5-class head** (`NUM_CLASSES_LIST: [5]`, `LOAD_BACKBONE_ONLY: true`), so its
> `SERVER_CHECKPOINT` (`pretrained_weights.pt`) must have the `omni_heads.*` **stripped** — otherwise
> the server persistor's `strict=False` load hits a 14-vs-5 shape mismatch on `omni_heads.0` and the
> model goes straight to `ERROR` (see Troubleshooting). Produce the backbone-only file with the
> finetuning tutorial's `make prepare-checkpoint` (`process_tools/preprocess_checkpoints.py` strips the
> heads); **do not** just copy the eval tutorial's checkpoint across.

---

## Hardware & capacity requirements

These experiments are far heavier than a stock FLIP run — the Ark+ model is a `swin_large_384`
backbone run at 768 px, and the pretrained checkpoint is ~759 MiB (~1.5 GiB for the multimodel
variant). Several defaults sized for small models **must be raised**, or jobs OOM / time out.

**Central Hub (ECS Fargate) — sizes in `deploy/providers/AWS/ecs_tasks.tf`:**

| Task | Stock default | Ark+ requirement | Why |
|---|---|---|---|
| `fl-api-net-1` | 0.5 vCPU / 1 GiB | **1 vCPU / 4 GiB** | Streams the 759 MiB checkpoint from S3 to the shared EFS volume; OOM-kills at 1 GiB |
| `fl-server-net-1` | 1 vCPU / 2 GiB | **2 vCPU / 8 GiB** | `torch.load`s the 759 MiB checkpoint as the initial/eval model; OOM-kills at 2 GiB (surfaces as `Server disconnected without sending a response` + a model `ERROR`) |

Also on the hub:
- **`MAX_MODEL_FILE_BYTES = 5 GiB`** (the current Settings default) — the presigned-POST upload
  cap must admit the ~759 MiB / ~1.5 GiB checkpoints.
- **Shared checkpoint-staging EFS volume** (`fl_checkpoints` access point) mounted at
  `/app/server-checkpoints` on **both** fl-api (writer) and fl-server (reader), with
  `SERVER_CHECKPOINT_ROOT` set — large checkpoints are staged server-side, never bundled into the
  client app (FLIP#695). Applies to eval `checkpoint`s **and** training `SERVER_CHECKPOINT`s.
- **Per-job GPU resource spec** in `.env.stag`: `JOB_RESOURCE_SPEC_NUM_GPUS=1`,
  `JOB_RESOURCE_SPEC_MEM_PER_GPU_IN_GIB=7`, so NVFLARE schedules the job onto a GPU.

**Trusts — both run on the GPU desktop, not the trust EC2:**
- **One CUDA GPU per trust**, ≥ ~8 GiB VRAM (the job requests 7 GiB/GPU), for `swin_large_384`
  @ 768 px + the 759 MiB model. The compose GPU overlay reserves a host GPU per fl-client. The
  trust EC2 (`t3.xlarge`) is **GPU-less** — the sole reason these experiments run the trusts on the
  desktop instead.
- **Disk** for the on-demand DICOM downloads (`trust/data/<KIT>/net-1`) plus the pulled studies in
  each trust's Orthanc + XNAT (the finetuning **train** cohort is ~1900 studies/site vs ~475 for
  the holdout).
- **Finetuning is compute-heavy**: 50 global × 2 local rounds × ~1900 studies/site of swin-large
  forward passes ≈ **several hours** per trust GPU even with the backbone frozen. Reduce
  `GLOBAL_ROUNDS` in the tutorial `config.json` for a quick end-to-end validation.

---

## Part A — Deploy the FLIP hub (AWS staging, ECS Fargate)

The Central Hub runs on **ECS Fargate** (flip-api + fl-api-net-1 + fl-server-net-1); the EC2 is a
bastion only. From `deploy/providers/AWS/` with the staging profile:

```bash
cd deploy/providers/AWS
export AWS_PROFILE=stag                 # must match; the Makefile enforces it
make aws-login                          # SSO
make plan  PROD=stag DEPLOY_TRUST_EC2=false   # hub-only (trusts run on the desktop)
make apply PROD=stag DEPLOY_TRUST_EC2=false   # run plan + apply as SEPARATE commands
make deploy-centralhub                  # roll the hub services to the develop tip (sha-<short7> task-def revisions)
make deploy-ui                          # ship the UI (S3 + CloudFront)
```

Image tags come from `.env.stag`: `DOCKER_TAG` (flip-api / flip-ui) and `DOCKER_FL_TAG`
(fl-api / fl-server / fl-client). GPU jobs need `JOB_RESOURCE_SPEC_NUM_GPUS`/
`JOB_RESOURCE_SPEC_MEM_PER_GPU_IN_GIB` in `.env.stag` (1 / 7 for Ark+).

**FL-task memory (important for Ark+):** the fl-api stages the 759 MiB checkpoint and the
fl-server torch-loads it, so `ecs_tasks.tf` sizes `fl-api-net-1` at **4 GiB** and
`fl-server-net-1` at **8 GiB** (1 GiB / 2 GiB OOM-kill on the Ark+ weights).

Verify: `curl -s -o /dev/null -w '%{http_code}\n' https://stag.flip.aicentre.co.uk/api/health` → `200`.

---

## Part B — Bring up the two trusts (on the GPU desktop)

Both trusts run on the desktop (they need the GPU the trust EC2 lacks). Prereqs: the trusts are
registered on the hub (`make register-trusts PROD=stag`) and the FL-server NLB admits the desktop's
public IP (`make allow-local-trust-nlb PROD=stag`). The DeCaf data itself is loaded into each
trust's Orthanc + OMOP **after** the trust containers are up — see
[Load the DeCaf dataset](#load-the-decaf-dataset-into-omop--orthanc) below.

**Two trusts on one host** — give each kit *distinct host-published ports* and point observability
at the in-repo config (the production compose defaults to an EC2 path). In `trust/.env.<KIT>.stag`:

```ini
# GSTT (Trust_1) uses 8001/8010/8020; give KCH (Trust_2) non-colliding ports:
IMAGING_API_PORT=8003
DATA_ACCESS_API_PORT=8012
TRUST_API_PORT=8022
# both kits, so alloy/loki/grafana mount the real configs:
OBSERVABILITY_CONFIG_DIR=/…/FLIP-Ark/trust/observability
```

Start them (XNAT's reset step needs `sudo`, so run in a real terminal or enable passwordless sudo):

```bash
env PROD=stag make -C trust up-trust KIT=GSTT
env PROD=stag make -C trust up-trust KIT=KCH
```

Verify both are polling the hub: `docker logs trust1-trust-api-1 --tail 3` should show
`GET https://stag.flip.aicentre.co.uk/api/tasks/pending "HTTP/1.1 200 OK"`. XNAT is repopulated
from Orthanc on demand during the image pull, so a fresh XNAT is fine.

### Load the DeCaf dataset into OMOP + Orthanc

Once a trust's containers are up, its OMOP is an empty schema and its Orthanc is an empty PACS.
`scripts/load_xray_omop_dataset.py` on the `add-xray-omop-dataset-loader` branch populates both
from a DeCaf site folder in one pass — check that branch out (or cherry-pick the one script) to run
it; it isn't duplicated onto this branch. Per accession it writes the MI-CDM chain
(`person → visit_occurrence → procedure_occurrence → image_occurrence`) plus one
`image_feature`/`observation` pair per pathology label into OMOP, and uploads the DICOM to Orthanc
unchanged — the DICOM's own `AccessionNumber` becomes the OMOP `accession_id`, so imaging-api can
retrieve the study PACS-side by the same identifier `flip.get_dataframe` returns. It's idempotent
(safe to re-run) and reads DB/PACS credentials straight from a trust kit file via `--env-file`.

The DeCaf `balanced_synthetic_split/` ships one **train** folder and one **holdout** folder per
site (`re_final_site<N>` / `re_final_site<N>_holdoff`) — this is exactly the train-vs-holdout split
the three experiments key off (see the cohort-discriminator column in the experiments table at the
top): the loader auto-tags any folder whose name contains `holdoff`/`holdout` with the distinct
`procedure_source_value = 'Chest X-ray (holdout)'`, and everything else with `'Chest X-ray'`. Load
both folders for each site, mapping site → trust (site1 → GSTT/Trust_1, site2 → KCH/Trust_2):

```bash
DATA=~/data/DeCaf_MICCAI_2026/balanced_synthetic_split   # adjust to your local copy

# GSTT / Trust_1 — train + holdout
uv run scripts/load_xray_omop_dataset.py \
  --folder "$DATA/re_final_site1" \
  --env-file trust/.env.GSTT.stag --trust-label Trust_1

uv run scripts/load_xray_omop_dataset.py \
  --folder "$DATA/re_final_site1_holdoff" \
  --env-file trust/.env.GSTT.stag --trust-label Trust_1

# KCH / Trust_2 — train + holdout (folder names follow the same site<N>[_holdoff] pattern)
uv run scripts/load_xray_omop_dataset.py \
  --folder "$DATA/re_final_site2" \
  --env-file trust/.env.KCH.stag --trust-label Trust_2

uv run scripts/load_xray_omop_dataset.py \
  --folder "$DATA/re_final_site2_holdoff" \
  --env-file trust/.env.KCH.stag --trust-label Trust_2
```

`--env-file` reads `OMOP_POSTGRES_USER`/`_PASSWORD`/`_DB` + `OMOP_DB_PORT` (must be the
write-capable role, not `DATA_ACCESS_*`) and `ORTHANC_USERNAME`/`_PASSWORD` + `PACS_UI_PORT` from
the kit; hosts default to `localhost` (override with `--db-host`/`--orthanc-host` for a different
topology than "loader running on the same desktop as the trust containers"). Useful flags for a
quick check before committing to a full load: `--dry-run` (report without writing), `--limit N`
(first N accessions only), `--skip-omop` / `--skip-orthanc` (populate one side only).

---

## Part C — Run an experiment (e2e driver)

The `flip-api/tests/e2e_smoke.py` harness drives the full lifecycle (create project → cohort →
image pull → upload app + checkpoint → train/eval → wait). It talks only to the hub API, so it can
target **staging** via `FLIP_E2E_BASE_URL`.

**Auth on staging:** the harness authenticates as the admin via Cognito. Staging disables MFA
(`ENFORCE_MFA=false` on the flip-api task + the admin user's TOTP disabled) so password auth works.
Because the flip-api Makefile force-loads `.env.development`, **force the stag Cognito values on the
command line** so they override it:

```bash
cd flip-api
TUT=../.claude/worktrees/arkplus-apps-atb-2/fl-tutorials/nvflare   # adjust to your tutorial checkout

AWS_COGNITO_USER_POOL_ID=eu-west-2_WdbAxJrH3 \
AWS_COGNITO_APP_CLIENT_ID=bb4qqejptnt3bac342itt7n0f \
AWS_REGION=eu-west-2 AWS_PROFILE=stag \
ADMIN_USER_PASSWORD="$(grep '^ADMIN_USER_PASSWORD=' ../.env.stag | cut -d= -f2-)" \
FLIP_E2E_BASE_URL=https://stag.flip.aicentre.co.uk/api \
DB_HOST=localhost \
.venv/bin/python -m tests.e2e_smoke \
  --model-files-dir "$TUT/image_evaluation/arkplus_baseline_classification_evaluation/app_files" \
  --query-file     "$TUT/image_evaluation/arkplus_baseline_classification_evaluation/query.sql" \
  --project-name "Ark+ Baseline Eval" \
  --model-name   "Ark+ Baseline Eval" \
  --no-dicom-to-nifti \
  --training-start-timeout 1200
```

Key flags (all optional, sensible defaults):

- `--project-name` / `--model-name` — name each experiment distinctly (reused per run).
- `--no-dicom-to-nifti` — the Ark+ apps read DICOMs directly (`ResourceType.ALL`); disable the
  XNAT dcm2niix conversion. **Set at project creation; immutable afterwards** (the edit-project
  form shows it read-only).
- `--project-id <UUID>` — reuse an already-approved project to **skip cohort submission + the
  image pull** (printed as `project_id=…` on the first run); great for iterating on training/eval
  code without re-pulling ~6 min of DICOM per backend.
- `--training-start-timeout 1200` — large-checkpoint jobs (upload + de-bundle) exceed the 300 s
  default; give them 1200 s.

### The three experiments (swap the tutorial + name)

```bash
# 1) Baseline evaluation — holdout dataset
--model-files-dir "$TUT/image_evaluation/arkplus_baseline_classification_evaluation/app_files" \
--query-file     "$TUT/image_evaluation/arkplus_baseline_classification_evaluation/query.sql" \
--project-name "Ark+ Baseline Eval" --model-name "Ark+ Baseline Eval"

# 2) Finetuning — finetuning (train) dataset
--model-files-dir "$TUT/image_classification/arkplus_fine_tuning/app_files" \
--query-file     "$TUT/image_classification/arkplus_fine_tuning/query.sql" \
--project-name "Ark+ Finetuning" --model-name "Ark+ Finetuning"

# 3) Multimodel evaluation — holdout dataset (evaluates pretrained + finetuned)
--model-files-dir "$TUT/image_evaluation/arkplus_multimodel_classification_evaluation/app_files" \
--query-file     "$TUT/image_evaluation/arkplus_multimodel_classification_evaluation/query.sql" \
--project-name "Ark+ Multimodel Eval" --model-name "Ark+ Multimodel Eval"
```

For (3), stage the finetuned checkpoint produced by (2) into the multimodel `app_files/` as
`arkplus_finetuned_weights.pt` (referenced by `models.arkplus_finetuned.checkpoint` in the
multimodel `config.json`, alongside the pretrained `arkplus_pretrained_weights.pt`). **Unwrap it
first:** the finetuning run's downloadable result is `FL_global_model.pt`, a *wrapped*
`OrderedDict({'model': <state_dict>, 'train_conf': …})`, but the evaluation job loads a **bare**
state dict — extract the inner `state_dict` and re-save:

```python
import torch
# weights_only=True: FL_global_model.pt is only tensors + a plain config dict, so load it safely
# (avoids unpickling arbitrary objects).
ck = torch.load("FL_global_model.pt", map_location="cpu", weights_only=True)   # from the finetune results zip
torch.save(ck["model"], "arkplus_finetuned_weights.pt")                        # 333-tensor bare state_dict; omni_heads.0 = (5, 1376)
```

The keys must match the eval model (`arkplus_singlehead`, `NUM_CLASSES_LIST: [5]`); a wrapped dict or
a head-shape mismatch makes the server persistor load fail and the model go straight to `ERROR`.

Results (cross-site metrics) are on the model's page in the UI, or via
`GET /api/model/<model_id>/metrics`.

---

## Troubleshooting (issues hit while validating this on staging)

| Symptom | Cause | Fix |
|---|---|---|
| `Bind for 0.0.0.0:80xx failed: port is already allocated` on 2nd trust | Both kits publish the same API ports | Give KCH distinct `IMAGING_API_PORT`/`DATA_ACCESS_API_PORT`/`TRUST_API_PORT` |
| `alloy` mount error `.../config.alloy … not a directory` | Prod compose defaults `OBSERVABILITY_CONFIG_DIR` to the EC2 path | Set `OBSERVABILITY_CONFIG_DIR=<repo>/trust/observability` in the kit |
| `xnat-reset … sudo: a terminal is required` | XNAT reset `rm -rf`s root-owned dirs | Run `up-trust` in a real terminal, or enable passwordless sudo |
| Create project → `500 Internal server error during authentication` | Token from the wrong Cognito pool (dev vs stag) | Force the stag `AWS_COGNITO_*` on the command line (Part C) |
| Auth returns an MFA challenge (`SOFTWARE_TOKEN_MFA`) | Cognito user has TOTP enrolled | Disable the stag admin user's MFA (stag only; prod keeps MFA) |
| `Unknown job_type: evaluation_client_api` | Base app / manifest not present in the hub's local `FL_APP_BASE_DIR` template tree (FLIP#724 — templates ship in the flip-api image, no S3 sync) | Ensure the flip-api image carries the target `fl-apps/` revision (rebuild + redeploy for a template change); in dev the `../fl-apps` bind-mount covers it |
| Model → `ERROR`, `Server disconnected without sending a response` | fl-api/fl-server OOM loading the 759 MiB checkpoint | Size `fl-api-net-1` ≥ 4 GiB, `fl-server-net-1` ≥ 8 GiB (`ecs_tasks.tf`) |
| **Finetuning** model → `ERROR` right after `init_training` (never reaches a `train` task); fl-server log shows `RuntimeError: Error(s) in loading state_dict for ArkPlusNVFlareWrapper: size mismatch for ark_model.omni_heads.0.weight: copying a param with shape torch.Size([14, 1376]) … current model is torch.Size([5, 1376])` | The `SERVER_CHECKPOINT` staged for finetuning is **not backbone-only** — it still carries the foundation model's 14-class `omni_heads.*`. The server persistor loads it `strict=False`, which tolerates *missing/unexpected* keys but **not a shape mismatch on a shared key**, so the 14-class head can't seat in the fresh 5-class model. Typically caused by pointing `pretrained_weights.pt` at the **eval** checkpoint (which keeps the heads) instead of the finetuning one. | Use a **backbone-only** `pretrained_weights.pt` — produce it via the finetuning tutorial's `make prepare-checkpoint` (`process_tools/preprocess_checkpoints.py`, which strips `omni_heads.*`). Quick fix if you only have a head-carrying copy: `torch.load` it and re-save `{k:v for k,v in sd.items() if not k.startswith("omni_heads.")}`. See the finetuning-checkpoint note under **The three experiments**. |
| Eval reports `RESULTS_UPLOADED` but **metrics are empty**; fl-client log shows `RuntimeError: No DICOM image/label pairs found`, and imaging-api logs `Trust-side storage error … [Errno 13] Permission denied: '/app/data/images/net-1/…-scans-ALL.zip'` | The eval-time per-accession DICOM download writes into the shared bind mount `trust/data/<KIT>` → `/app/data/images` (host uid 1001), but its `net-1/` subdir was created by the **root** fl-client, so imaging-api (uid 1000) can't write DICOMs into it. `get_dataframe`/`project_id` are fine — only the image write fails, so every accession is skipped and the datalist is empty. | Make the per-net dir writable by imaging-api's uid on each trust (container-root = host-root on the bind mount, so no host `sudo`): `docker exec -u 0 <trust>-imaging-api-1 chown -R 1000:1000 /app/data/images/net-1`. Durable — `CleanupImages` only clears the dir's *contents*, never re-creates it. |
| **Finetuning** (`standard_client_api`) model OOMs during **post-training cross-site validation** — all `train`/aggregation rounds finished (metrics reach the last local epoch), then fl-server `MemoryUtilized` spikes to the 8 GiB task ceiling and the job child process exits; the model **orphans at `TRAINING_STARTED`** (crash lands before `END_RUN`, so `PersistToS3AndCleanup` never runs → empty S3 results, net stays `BUSY`). Prod-only — staging (both trusts on one fast-local desktop) doesn't hit it. | The stock `CrossSiteModelEval` runs a `submit_model` all-to-all (each client's full model collected server-side **plus** the global model broadcast), and over a **slow-link trust** (e.g. BDMS/Thailand) the full ~759 MiB copies stay resident for minutes → OOM (~97 % of 8 GiB). | Finetune eval now defaults to **`GlobalModelEval`** (broadcasts only the aggregated global model; no `submit_model` matrix) — peak memory drops to ~75 % and the run completes to `RESULTS_UPLOADED`. To clear an already-orphaned model + free the net: `POST /fl/stop/{model_id}`. Head-only eval broadcast (only the trained head crosses the wire) is a further optimisation. |

## Results — baseline evaluation (validated on staging, 2026-07-03)

Pretrained Ark+ checkpoint scored against each trust's **holdout** cohort
(GSTT/Trust_1 n=478, KCH/Trust_2 n=468). Read from
`evaluation_results/evaluation_results.json` in the downloaded results zip.

> **Shape change (FLIP#754).** Runs from that fix onwards nest the metrics one level deeper —
> `{"<trust>": {"SRV_<model_name>": {...}}}` rather than `{"<trust>": {...}}`. Keying on the trust
> alone made each evaluated model overwrite the previous one, so a multimodel evaluation only ever
> reported its last model. The zip also carries `evaluation_results/evaluation_failures.json`, and a
> run whose every `validate` task fails now ends in `ERROR` instead of `RESULTS_UPLOADED`. The table
> below predates the change and is single-model, so its numbers are unaffected.

| Lesion | GSTT (Trust_1) AUROC | KCH (Trust_2) AUROC |
|---|---|---|
| Effusion | 0.998 | 1.000 |
| Consolidation | 0.989 | 0.998 |
| Infiltration | 0.863 | 0.878 |
| Lung Nodule or Mass | 0.970 | 0.955 |
| Pneumothorax | 0.971 | 0.997 |
| **Mean** | **0.958** | **0.966** |

`project_id`, cohort decryption and `flip.get_dataframe` all work on the platform — the
per-site datalist is fetched from the trust APIs and the DICOMs are pulled on demand at eval
time (see the permission-denied troubleshooting row above, which was the one real blocker).

> **Results zip no longer bundles the evaluated model.** `CrossSiteModelEval` writes the
> server-loaded checkpoint to `cross_site_val/model_shareables/SRV_<name>` (~759 MiB for the
> Ark+ baseline) purely to broadcast it to clients for scoring — it is an *input* the user
> already uploaded, not an eval output. `PersistToS3AndCleanup` (flip-utils) now prunes that
> directory before zipping. Verified on stag (fl-server image `62f0fb1e`): the same baseline-eval
> results zip dropped from **737 MiB to ~10 KB**, with the metrics
> (`evaluation_results/evaluation_results.json`) and the small per-site raw shareables
> (`cross_site_val/result_shareables/`) retained and the model gone.

## Results — finetuning (validated on staging, 2026-07-04)

Frozen-backbone finetune of the pretrained Ark+ checkpoint onto a fresh 5-class DeCaf head
(`AGGREGATE_ONLY_REGEX: "omni_heads"`, `LOAD_BACKBONE_ONLY: true`), on the **finetuning** (train)
cohort. This validation run used a reduced `GLOBAL_ROUNDS: 5` (× `LOCAL_ROUNDS: 2`) — see the
capacity note above for the full-scale default (50 global rounds) and why it takes several hours.

Training completed cleanly to `RESULTS_UPLOADED` on the deployed image (`acfbd280`, model
`7d179939`). Previously this OOM-crashed the fl-server mid-round-3 (unbounded per-round RSS
growth from re-broadcasting the ~759 MiB backbone every round). Two platform fixes made this run
possible, exercised together for the first time here:

- **Memory (Level 1).** FLIP's `ScatterAndGather` controller is now a thin subclass of NVFLARE's
  stock controller, inheriting `memory_gc_rounds` allocator-aware cleanup (`gc.collect` + glibc
  `malloc_trim`) — bounding server RSS across rounds instead of leaking ~1.3 GiB/round.
- **Broadcast (Level 2).** After round 0 the frozen backbone doesn't change, so it isn't
  re-broadcast. The fl-server log confirms this on every round after the first:
  `TrimBroadcastVars: broadcasting 2 of 333 var(s) matching 'omni_heads' at round N` — the 759 MiB
  backbone went out once, not five times; clients reconstructed the full model locally from it and
  trained normally.

There's no cross-site metric from training itself (no held-out split is scored during `standard_client_api`
training) — the finetuned checkpoint is scored downstream in the multimodel evaluation below,
which is what actually demonstrates the finetune improved the model.

> **Reproduced on production (GSTT + BDMS), 2026-07-07.** On prod the finetune's post-training
> cross-site validation surfaced a **third** memory issue not seen on staging: broadcasting the full
> ~759 MiB model to the clients OOM-crashed the fl-server (the slow BDMS/Thailand link kept the
> full-model copies resident for minutes, pushing `MemoryUtilized` to ~97 % of the 8 GiB task).
> Because the crash landed *before* NVFLARE's `END_RUN`, results never persisted and the model
> orphaned at `TRAINING_STARTED` (net wedged `BUSY`). Fixed by switching finetune eval to
> **`GlobalModelEval`** (only the aggregated global model is broadcast; the `submit_model` all-to-all
> is dropped) — peak memory fell to ~75 % and the run completed cleanly. See the cross-site-val OOM
> row in Troubleshooting.

## Results — multimodel evaluation: finetuned vs. pretrained (validated on staging, 2026-07-04)

Evaluates the pretrained Ark+ checkpoint **and** the finetuned checkpoint from the run above,
head-to-head, against the same **holdout** cohort used for the baseline evaluation above (reusing
that project — `model_id ed2d49b5`, image `acfbd280`). The finetune is at least as good on every
lesion at both trusts, and the gain survives Benjamini-Hochberg correction on 6 of the 10
lesion–trust pairings; the exceptions are the four where the pretrained model already sits at or
above 0.997, which leaves it no headroom. Significance is a two-sided DeLong test, BH-corrected per
trust (critical *p* = 2.7×10⁻² at GSTT, 7.2×10⁻⁷ at KCH). The figures below are the 50-round
production finetune — the staging run above used a shorter 5-round one and is not tabulated
separately.

**GSTT (Trust_1, n=478)**

| Lesion | Pretrained AUROC | Finetuned AUROC | Δ | DeLong *p* |
|---|---|---|---|---|
| Effusion | 0.998 | 1.000 | +0.002 | 0.12 (n.s.) |
| Consolidation | 0.989 | 0.997 | +0.008 | 0.027 |
| Infiltration | 0.863 | 0.998 | +0.135 | 1.8×10⁻¹⁴ |
| Lung Nodule or Mass | 0.970 | 1.000 | +0.030 | 5.9×10⁻⁶ |
| Pneumothorax | 0.971 | 1.000 | +0.029 | 0.0065 |
| **Mean** | **0.958** | **0.999** | **+0.041** | |

**KCH (Trust_2, n=468)**

| Lesion | Pretrained AUROC | Finetuned AUROC | Δ | DeLong *p* |
|---|---|---|---|---|
| Effusion | 1.000 | 1.000 | +0.000 | 1.00 (n.s.) |
| Consolidation | 0.998 | 1.000 | +0.002 | 0.31 (n.s.) |
| Infiltration | 0.878 | 1.000 | +0.122 | 1.2×10⁻¹⁵ |
| Lung Nodule or Mass | 0.955 | 1.000 | +0.045 | 7.2×10⁻⁷ |
| Pneumothorax | 0.997 | 1.000 | +0.003 | 0.19 (n.s.) |
| **Mean** | **0.966** | **1.000** | **+0.034** | |

The biggest single gain is Infiltration (0.863 → 0.998 at GSTT, 0.878 → 1.000 at KCH) — the weakest
pretrained score at both trusts in the baseline evaluation above. The finetuned model saturates both
cohorts (AUROC ≥ 0.997), so these numbers speak to the platform working end to end, not to a
state-of-the-art result on a synthetic task. Results zip was ~12 KB
(`evaluation_results/evaluation_results.json` only — the same results-zip prune from the baseline
evaluation applies here too).
