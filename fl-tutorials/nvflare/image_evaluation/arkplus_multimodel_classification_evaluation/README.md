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

# Multi-model Ark+ Chest X-ray Evaluation (NVFLARE Client API) — FLIP tutorial

FLIP tutorial for federated evaluation of **multiple** Ark+ models (a zero-shot foundation model and a
fine-tuned model) on chest X-ray classification, using the **NVFLARE Client API**. The same hold-out data
at each site is scored against every model, and per-lesion AUROC plus pairwise **DeLong** statistical
comparisons between the models are reported.

This tutorial replaces the previous executor-based implementation (`class FLIP_EVALUATOR(Executor)` +
the bespoke `ModelEval`/`EvaluationPTModelLocator` server flow, where **all** checkpoints arrived
unwrapped from a single `DataKind.COLLECTION` DXO), which is now deprecated. Here `evaluator.py` is a
plain `nvflare.client` script (`flare.init/receive/send`) and the job is a Python `FlipEvalRecipe` driven
by `job.py` rather than a job-type template + Docker harness.

The checkpoints stay **server-side**: `EvaluationModelLocator` loads every entry of
`config.json['models']` on the server and `GlobalModelEval` broadcasts each one as its own `validate`
task, named in the broadcast's DXO meta (`flip_eval_model_name`). The evaluator scores each broadcast on
the shared local cohort, caches the per-sample scores across tasks, and replies with the metrics of every
model scored so far — so the reply to the final task carries all per-model AUROCs plus the pairwise
DeLong map, which is what the server's last-reply-wins `EvaluationJsonGenerator` writes out. The
`evaluation_results.json` output contract (per-site, per-model metrics + DeLong) is unchanged from the
legacy tutorial.

> **Scope.** The same code path runs on the **local NVFLARE simulator / `uv` workflow** (`make run`,
> where `job.py` bundles the `.pt` files for the server-side locator) and on the **production FL
> platform** (where the FL API de-bundles uploaded checkpoints onto the hub staging volume — nothing
> client-side ever opens a `.pt` file in either mode). The FL server must run a `flip` package recent
> enough to name its evaluation broadcasts (`EvaluationModelLocator` stamping `flip_eval_model_name`);
> the evaluator fails with that remedy if the meta is absent.

## Compatible job type

This tutorial is designed for `JOB_TYPE=evaluation_client_api`.

## Prerequisites

- Python 3.12+
- A GPU (the Ark+ Swin-Large models run at 768×768) for a SimEnv run — both models are held on the
  device together, so this is more memory-hungry than the single-model baseline
- Access to the Ark+ foundation-model checkpoint (see checkpoint setup below); the fine-tuned checkpoint
  is downloaded automatically

## Dataset setup

This app expects a DECAF-formatted chest X-ray dataset with:

- A per-site CSV dataframe with `accession_id` and lesion labels
- Images organised under `<images_dir>/<accession_id>/...` (DICOM)

The quickest path uses the published reference dataset on Hugging Face. From the repo root:

```bash
make -C fl-tutorials download-arkplus-eval-data   # fetch + lay out data/arkplus/site{1,2}_holdoff/
make -C fl-tutorials run-tutorial TUTORIAL=arkplus_multimodel_classification_evaluation
```

`download-arkplus-eval-data` pulls the `site1_holdoff`/`site2_holdoff` hold-out splits of
[`aicentreflip/tutorials-arkplus-cxr-classification`](https://huggingface.co/datasets/aicentreflip/tutorials-arkplus-cxr-classification)
and normalises them into `fl-tutorials/nvflare/data/arkplus/site{1,2}_holdoff/` (gitignored), matching
this tutorial's `.env.app` defaults (the same hold-out splits the baseline tutorial evaluates).

For local development, per-site paths are set in `.env.app`:

- `DEV_IMAGES_DIR` / `DEV_DATAFRAME` (single-site dev default)
- `SITE1_IMAGES_DIR` / `SITE1_DATAFRAME`, `SITE2_IMAGES_DIR` / `SITE2_DATAFRAME`
  (per-site, for the 2-client simulation)

### Per-site data in the simulator

Unlike the previous executor-based implementation (whose testing harness Docker-mounted each site's data
into the containers), the Client-API SimEnv runs **in-process with no Docker
mounts**. Per-site data is therefore selected inside the evaluator: it calls `flare.get_site_name()`
(`site-1`/`site-2`) and `app_files/data_utils.py` resolves the matching `SITE{N}_IMAGES_DIR` /
`SITE{N}_DATAFRAME` from `.env.app` (falling back to the single `DEV_*` paths). So `site-1` and `site-2`
score **different** hold-out sets.

### Simulator vs. real deployment

The per-site local paths are **simulator-only**. On a real federated client the fl-client runs with
`LOCAL_DEV=false`, and the data layer ignores the local paths entirely: the cohort dataframe comes from
`FLIP().get_dataframe(project_id, query)` and DICOMs from `FLIP().get_by_accession_number(...)` (the
trust's data-access-api / imaging-api). `project_id`/`query` are supplied by the FL job config —
`project_id` via the evaluator's `--project_id {project_id}` arg (substituted by the FLIP-API) and
`query` via the top-level `query` key of `config_fed_client.json` (read by `evaluator.load_query()`). The
switch is keyed on `LOCAL_DEV` in `app_files/data_utils.py` (`_is_local_dev`).

The cohort query for a real deployment is [`query.sql`](query.sql) — it selects the **hold-out** chest
X-ray set (`procedure_source_value = 'Chest X-ray (holdout)'`) and returns the seven lesion-label columns
both models are scored against.

### Image orientation

> **⚠️ MONAI's `LoadImaged` returns the DICOM pixel array transposed, and this app undoes that.** The
> array arrives indexed `(column, row)` where `PixelData` is `(row, column)`, so a chest radiograph
> loads **on its side**. `get_xray_transforms` in `app_files/data_utils.py` corrects it with
> `Transposed(keys=["image"], indices=(0, 2, 1))`, placed after the channel-first step and before
> anything spatial runs. The leading `0` is the channel axis added by `_ensure_image_channel_first`, so
> the permutation assumes that step has already run — moving it literally adjacent to `LoadImaged` hands
> a 3-element permutation to a 2-D array. If you change the reader, the dataset or the image format,
> re-check this step: it is a property of this loading chain, not a universal correction.

Ark+ is pretrained on upright chest radiographs, so a sideways (or mirrored) image is a large
distribution shift that **silently** depresses every reported metric — nothing errors or warns, the
AUROCs are just quietly worse. Because this app scores two models head-to-head, an orientation fault
also distorts the *comparison* — and it need not hit both models equally. Re-scoring the production
checkpoints over one hold-out cohort in both orientations (FLIP#820), the pretrained model's mean AUROC
rose from 0.827 to 0.958 once the images were upright, while the fine-tuned model — already saturated
near 1.0 on this synthetic task — did not move, so the apparent gap between them collapsed from 0.172 to
0.041 without either checkpoint changing. (The staging run tabulated in
[`ARKPLUS_EXPERIMENTS_GUIDE.md`](../../ARKPLUS_EXPERIMENTS_GUIDE.md) used a different, shorter finetune,
so its figures differ in the third decimal. Do not read across the two runs.)

Do not check this by eye: a mirrored chest X-ray looks entirely plausible unless you read the cardiac
silhouette or the laterality marker. Verify it **numerically** — compare the transform output against an
array read straight from `pydicom`'s `PixelData`. Note that no rotation or flip substitutes for the
transpose: `Rotate90d(k=-1)` composed with the loader transpose leaves the image upright but mirrored,
and `Flipd` leaves it on its side — the variant this app shipped, and why the fault went unnoticed for
so long.

## Checkpoint setup

The app evaluates two models, so it needs two clean `.pt` checkpoints in `app_files/`: the foundation
model `arkplus_pretrained_weights.pt` and the fine-tuned model `arkplus_finetuned_weights.pt`. Both are
loaded by every client's evaluator. `make run`/`make export` prepare both automatically (they run
`prepare-checkpoint` first).

### Foundation model (arkplus_pretrained)

The zero-shot Ark+ checkpoint is produced from the raw Ark6 training output
(`Ark6_swinLarge768_ep50.pth.tar`) in two steps — identical to the
[baseline tutorial](../arkplus_baseline_classification_evaluation/README.md):

1. **Fetch the raw checkpoint** (once):

   ```bash
   make download-raw-checkpoint
   ```

   This downloads it to the path given by `RAW_CHECKPOINT` in `.env.app` (default
   `models/Ark6_swinLarge768_ep50.pth.tar`, an app-relative path). If you already have the file, point
   `RAW_CHECKPOINT` at it instead. Access is via [this form](https://forms.gle/qkoDGXNiKRPTDdCe8).

2. **Prepare it** — done automatically by `make run`/`make export`, or on demand with
   `make prepare-checkpoint`, which converts the raw checkpoint into the clean
   `arkplus_pretrained_weights.pt` (a no-op if it already exists). The conversion script lives at
   `process_tools/preprocess_checkpoints.py` — see [process_tools/README.md](process_tools/README.md) for
   the extraction and key-remapping details.

### Fine-tuned model (arkplus_finetuned)

The fine-tuned checkpoint is downloaded automatically from
[`aicentreflip/tutorials-arkplus-cxr-finetuned`](https://huggingface.co/aicentreflip/tutorials-arkplus-cxr-finetuned)
on Hugging Face by `make run`/`make export` if not already present — no manual steps required. To use
your own, set `FINETUNED_CHECKPOINT` in `.env.app` to a URL or a local (absolute) path.

## App configuration

Default local development settings are in `.env.app`:

- `JOB_TYPE=evaluation_client_api`
- `RAW_CHECKPOINT=models/Ark6_swinLarge768_ep50.pth.tar`
- `FINETUNED_CHECKPOINT=` (empty → download the default fine-tuned model from `aicentreflip/tutorials-arkplus-cxr-finetuned`)
- `DEV_IMAGES_DIR` / `DEV_DATAFRAME` and the per-site `SITE{1,2}_*` paths

Each model is defined in `app_files/arkplus_flat_models.py`, built by `app_files/models.py`
(`_build_arkplus_raw`), and registered in `config.json["models"]` (its `arkplus_config`, checkpoint file,
and head/label mapping). The mapping from a model's NIH-14 head outputs to the target DECAF lesions lives
in `app_files/data_utils.py` (`MAPPING_REGISTRY`).

`make export`/`make run` run `job.py` in the **flip-utils** environment with the `full` ML extra (the same
package set the `flare-fl-base` FL image installs) so a local run matches the deployed image.

## Run the tutorial

`job.py` drives the recipe in two modes.

```bash
make download-raw-checkpoint   # once: fetch the raw Ark6 foundation checkpoint into models/

# Export the complete NVFLARE job for review (no GPU needed)
make export                    # prepares both checkpoints, then writes ./fl_job/flip_evaluation/

# SimEnv local simulation (requires GPU + data + both checkpoints)
make run                       # prepares both checkpoints (if needed), then runs the simulator via `make sim`
```

The fine-tuned checkpoint is auto-downloaded from HuggingFace on the first prepare. To override either
source, set `RAW_CHECKPOINT` / `FINETUNED_CHECKPOINT` in `.env.app` (or pass on the CLI, e.g.
`make run FINETUNED_CHECKPOINT=/path/to/custom_finetuned.pt`).

Useful targets: `make prepare-checkpoint` (prepare both checkpoints only), `make clean` (removes `./fl_job`).

## Key files

- `app_files/evaluator.py`: the Client-API evaluation loop (score each named broadcast → per-lesion AUROC + DeLong).
- `app_files/arkplus_flat_models.py`: the `ArkSwinTransformer` model definition.
- `app_files/models.py`: model factory (`_build_arkplus_raw`, plus `get_model()` for the recipe's persistor).
- `app_files/metrics_utils.py`: AUROC and the DeLong pairwise test implementation.
- `app_files/data_utils.py`: data loading, DICOM parsing, label mappings, transforms, per-site resolution.
- `app_files/config.json`: per-model checkpoint/architecture mapping and evaluation settings.
- `job.py`: builds `FlipEvalRecipe`, stages the checkpoints into the server app, and runs export / SimEnv.

## Output metrics

The evaluator returns **aggregate** (cohort-level) metrics only — per-lesion AUROC per model plus pairwise
DeLong p-values and Benjamini-Hochberg (BH) FDR-adjusted q-values — collected by the server into
`evaluation_results.json` keyed by site then model:

```json
{
    "site-1": {
        "arkplus_pretrained": {
            "auroc_Effusion": 0.85,
            "auroc_Consolidation": 0.79,
            "auroc_Infiltration": 0.72,
            "auroc_Lung Nodule or Mass": 0.81,
            "auroc_Pneumothorax": 0.88,
            "delong_p_values": {
                "Effusion": { "arkplus_pretrained": 1.0, "arkplus_finetuned": 0.03 },
                "Consolidation": { "arkplus_pretrained": 1.0, "arkplus_finetuned": 0.15 },
                "Infiltration": { "arkplus_pretrained": 1.0, "arkplus_finetuned": 0.42 },
                "Lung Nodule or Mass": { "arkplus_pretrained": 1.0, "arkplus_finetuned": 0.07 },
                "Pneumothorax": { "arkplus_pretrained": 1.0, "arkplus_finetuned": 0.51 }
            },
            "delong_q_values": {
                "Effusion": { "arkplus_pretrained": 1.0, "arkplus_finetuned": 0.15 },
                "Consolidation": { "arkplus_pretrained": 1.0, "arkplus_finetuned": 0.25 },
                "Infiltration": { "arkplus_pretrained": 1.0, "arkplus_finetuned": 0.51 },
                "Lung Nodule or Mass": { "arkplus_pretrained": 1.0, "arkplus_finetuned": 0.18 },
                "Pneumothorax": { "arkplus_pretrained": 1.0, "arkplus_finetuned": 0.51 }
            }
        },
        "arkplus_finetuned": {
            "auroc_Effusion": 0.88,
            "auroc_Consolidation": 0.76,
            "auroc_Infiltration": 0.74,
            "auroc_Lung Nodule or Mass": 0.84,
            "auroc_Pneumothorax": 0.90,
            "delong_p_values": {
                "Effusion": { "arkplus_pretrained": 0.03, "arkplus_finetuned": 1.0 },
                "Consolidation": { "arkplus_pretrained": 0.15, "arkplus_finetuned": 1.0 },
                "Infiltration": { "arkplus_pretrained": 0.42, "arkplus_finetuned": 1.0 },
                "Lung Nodule or Mass": { "arkplus_pretrained": 0.07, "arkplus_finetuned": 1.0 },
                "Pneumothorax": { "arkplus_pretrained": 0.51, "arkplus_finetuned": 1.0 }
            },
            "delong_q_values": {
                "Effusion": { "arkplus_pretrained": 0.15, "arkplus_finetuned": 1.0 },
                "Consolidation": { "arkplus_pretrained": 0.25, "arkplus_finetuned": 1.0 },
                "Infiltration": { "arkplus_pretrained": 0.51, "arkplus_finetuned": 1.0 },
                "Lung Nodule or Mass": { "arkplus_pretrained": 0.18, "arkplus_finetuned": 1.0 },
                "Pneumothorax": { "arkplus_pretrained": 0.51, "arkplus_finetuned": 1.0 }
            }
        }
    },
    "site-2": {
        "...": "..."
    }
}
```

(Values above are **synthetic placeholders** chosen to show the JSON shape — not measured scores, and not
consistent with the real results referenced under [Image orientation](#image-orientation).)

### Fields

| Key | Type | Description |
|-----|------|-------------|
| `auroc_<Lesion>` | `float` | Area under the ROC curve for this lesion. Ranges `[0, 1]`; `NaN` if only one class is present in the ground truth. |
| `delong_p_values` | `dict[str, dict[str, float]]` | Pairwise DeLong p-values for each lesion, keyed first by lesion name then by the *other* model name. The diagonal (model vs. self) is hardcoded `1.0` as a sanity check; off-diagonal entries are the two-sided DeLong test. `NaN` if the lesion's cohort has only one class present, or if either class has fewer than 2 members (DeLong's variance estimate is inestimable with a single sample in either class). Only present when ≥ 2 models are configured. |
| `delong_q_values` | `dict[str, dict[str, float]]` | Benjamini-Hochberg FDR-adjusted q-values, same shape and diagonal convention as `delong_p_values`. Corrected **per model pair**, across that pair's lesion-level p-values — each pair is its own independent correction family, never pooled across multiple pairs. `NaN` wherever the corresponding `delong_p_values` entry is `NaN` (excluded from that pair's correction, not folded into the other lesions' ranks). Only present when ≥ 2 models are configured. |

Per-sample (row-level) predictions are deliberately **not** produced or exported: a per-patient list would
leak the exact evaluation cohort size and be linkable to individual patients. (The previous executor-based
implementation wrote per-sample CSVs to the run dir; this tutorial omits them and returns aggregate
metrics only.)

## Model code & `timm` compatibility

`app_files/arkplus_flat_models.py` adapts the `ArkSwinTransformer` from the original Ark+ repository
([jlianglab/Ark](https://github.com/jlianglab/Ark)), which pins **`timm==0.5.4`**. The model's `forward`
is kept identical to the upstream version (`forward_features` → projector → `omni_heads`).

There is a subtle cross-version gotcha. In **timm 0.5.4**, `SwinTransformer.forward_features` pooled
internally — it ended with `AdaptiveAvgPool1d(1)` and returned a per-image `(B, C)` vector — so the Ark
`forward` never needed to pool. In **modern timm (1.x)** that global average pool was **moved out** of
`forward_features` (into `forward_head`), and `forward_features` now returns the *unpooled* spatial map
`(B, H, W, C)` (a 24×24 grid for a 768px Swin).

This tutorial runs on modern timm, and the upstream `forward` bypasses `forward_head` (it uses its own
`omni_heads`). Without an explicit pool the heads emitted a **per-location** grid of outputs instead of one
prediction per image — producing mis-shaped predictions and making AUROC fail with
`ValueError: multi_class must be in ('ovo', 'ovr')`.

**Fix.** `forward` (and `generate_embeddings`) now apply an explicit global-average-pool over the spatial
dims right after `forward_features`, restoring the timm 0.5.4 behaviour and matching the Swin head's
default `global_pool='avg'`:

```python
x = self.forward_features(x)
x = self._global_pool(x)   # mean over spatial dims; no-op if already (B, C)
```

**Verified equivalent.** Holding the backbone fixed, the explicit pool was compared against an exact
replica of timm 0.5.4's `AdaptiveAvgPool1d(1)` pooling: the pooled features and all head outputs were
**bit-for-bit identical** (`max |Δ| = 0.0`), since averaging over the flattened token sequence (`L`) equals
averaging over the `H×W` grid (`L = H×W`). The fix is also a no-op if a future `timm` returns an
already-pooled `(B, C)` tensor.

## Notes and troubleshooting

- DeLong p-values below machine epsilon are reported as `0.0`. The test is two-sided (H₀: AUC_a = AUC_b).
- `delong_p_values` is only emitted when at least 2 models are configured in `config.json["models"]`.
- `delong_q_values` applies Benjamini-Hochberg FDR correction independently per model pair, across that
  pair's lesion-level DeLong p-values (5 tests per pair in this tutorial's config). If a third model is
  ever added, each new pair gets its own independent correction — q-values are never pooled across pairs.
- A lesion with a `NaN` p-value (either the cohort has only one class, or either class has fewer than 2
  members) is excluded from its pair's BH correction and reported as `NaN` in `delong_q_values`, not
  folded into the other lesions' ranks.
- **Interpreting `delong_q_values`**: a q-value is not a rescaled or "corrected" version of that same
  lesion's raw p-value — it is the smallest false-discovery-rate level at which this specific lesion's
  comparison would be called significant, considering it jointly with the rest of its pair's lesion
  tests. Concretely, `q <= 0.05` means "this finding survives if we're willing to accept a 5% false
  discovery rate across this pair's lesion comparisons" — not "there's a 5% chance of this AUROC gap
  under the null" (that's what the raw p-value alone would mean, without accounting for having run
  multiple simultaneous lesion tests). A q-value can therefore differ substantially from its own raw
  p-value (it's always `>= p`, and several lesions can share an identical q-value even with different
  raw p-values) — comparing `q` against your chosen significance level is the correct way to decide
  which lesions "survived" correction, not comparing the raw `delong_p_values` entries directly.
  **Why ties happen:** the step-up rule always rejects a contiguous prefix of the sorted ranks — if rank
  `k` crosses its own line `p_(k) <= (k/m)*Q`, every smaller rank `1..k-1` is swept into that same
  rejection *regardless of whether their own raw value would have crossed at that `Q`*. So rank `i`'s
  true q-value isn't "the `Q` at which `i` crosses its own line" — it's "the smallest `Q` at which *any*
  rank `j >= i` crosses its own line," i.e. `q_(i) = min` over `j >= i` of `p_(j) * m/j`, computed as a
  running minimum from the largest rank inward. Worked example with `m=3` sorted p-values `0.02, 0.025,
  0.03`: the per-rank raw values are `0.06, 0.0375, 0.03` (not ascending), but the running min from the
  back collapses all three to `q = 0.03` — rank 3's own crossing point is small enough to pull in ranks
  1 and 2 even though their individual raw values were larger. That's the mechanism, not a bug: a later,
  weaker-p lesion can still set the q-value for an earlier, stronger-p lesion in the same pair.
  You can also go the other direction, from a chosen FDR level back to a raw-p cutoff: pick a target
  `Q` for a given model pair, take every lesion in that pair with `q <= Q`, and the *largest* raw
  p-value among them is the critical cutoff for that `Q` — every `delong_p_values` entry at or below
  that cutoff is exactly the set that survives correction at `Q`. That's useful if you'd rather present
  a table of raw p-values (or the AUROC gap) and bold/flag whichever cells clear that single cutoff,
  stating `Q` and the derived cutoff together in the caption, instead of printing a `q` column.
- Both models are loaded onto the GPU together; if you hit an out-of-memory error, reduce `BATCH_SIZE` in
  `config.json` (it defaults to 1) or evaluate on a smaller GPU-fitting cohort.
