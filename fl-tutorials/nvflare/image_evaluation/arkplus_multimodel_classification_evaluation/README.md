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

# Comparing two chest X-ray models: pretrained vs fine-tuned

FLIP tutorial for federated evaluation of **multiple** Ark+ models (a zero-shot foundation model and a
fine-tuned model) on chest X-ray classification, using the **FLARE Client API**. The same hold-out data
at each site is scored against every model, and per-lesion AUROC plus pairwise **DeLong** statistical
comparisons between the models are reported.

`evaluator.py` is a plain `nvflare.client` script (`flare.init/receive/send`), and the job is a Python `FlipEvalRecipe` built by `job.py`.

The checkpoints stay **server-side**:

1. `EvaluationModelLocator` loads every entry of `config.json['models']` on the server.
2. `GlobalModelEval` broadcasts each model as its own `validate` task, named in the broadcast's DXO meta
   (`flip_eval_model_name`).
3. Each site's evaluator scores the broadcast on its local cohort, caches the per-sample scores across tasks,
   and replies with the metrics of every model scored so far.
4. The reply to the final task therefore carries all per-model AUROCs plus the pairwise DeLong map. The
   server's `EvaluationJsonGenerator` keeps each task's reply in `evaluation_results.json` (see
   [Output metrics](#output-metrics)).

> **Scope.** The same code path runs in two modes. In neither does anything client-side open a `.pt` file:
>
> - **Local FLARE simulator / `uv` workflow** (`make run`): `job.py` bundles the `.pt` files for the server-side locator.
> - **Production FL platform**: the FL API de-bundles the uploaded checkpoints onto the hub staging volume.
>
> The FL server must run a `flip` package recent enough to name its evaluation broadcasts (`EvaluationModelLocator` stamping `flip_eval_model_name`). If that meta is absent, the evaluator fails and names this remedy.

## At a glance

| Item | Value |
|---|---|
| What you upload | everything in `app_files/`, plus the two checkpoints from `make prepare-checkpoint` |
| Job type | evaluation, one validate task per model |
| Imaging | on, DICOM to NIfTI off |
| Cohort query | `query.sql` (the hold-out set) |
| Dataset | `make -C fl-tutorials download-arkplus-eval-data`, plus the raw Ark+ checkpoint (`make download-raw-checkpoint`) |
| Simulator | `make -C fl-tutorials run-tutorial TUTORIAL=arkplus_multimodel_classification_evaluation` (GPU; `MAX_SAMPLES=128` by default) |

Example radiographs, one per target lesion, are in the fine-tuning tutorial's
[`docs/dataset_examples.png`](../../image_classification/arkplus_fine_tuning/docs/dataset_examples.png)
(same dataset family; this tutorial scores the hold-out splits).

## Prerequisites

- Python 3.12+
- A GPU (the Ark+ Swin-Large models run at 768×768) for a SimEnv run — both models are held on the
  device together, so this is more memory-hungry than the single-model baseline
- Access to the Ark+ foundation-model checkpoint: request it with the [Ark+ checkpoint access form](https://forms.gle/qkoDGXNiKRPTDdCe8) (see [Checkpoint setup](#checkpoint-setup)). The fine-tuned checkpoint is downloaded automatically.

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
and normalises them into `fl-tutorials/data/arkplus/site{1,2}_holdoff/` (gitignored), matching
this tutorial's `.env.app` defaults (the same hold-out splits the baseline tutorial evaluates).

For local development, per-site paths are set in `.env.app`:

- `DEV_IMAGES_DIR` / `DEV_DATAFRAME` (single-site dev default)
- `SITE1_IMAGES_DIR` / `SITE1_DATAFRAME`, `SITE2_IMAGES_DIR` / `SITE2_DATAFRAME`
  (per-site, for the 2-client simulation)

### Per-site data in the simulator

The Client-API SimEnv runs **in-process with no Docker mounts**, so per-site data is selected inside the evaluator: it calls `flare.get_site_name()`
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

## Checkpoint setup

The app evaluates two models, so it needs two clean `.pt` checkpoints in `app_files/`: the foundation
model `arkplus_pretrained_weights.pt` and the fine-tuned model `arkplus_finetuned_weights.pt`. Both are
loaded **server-side** by `EvaluationModelLocator` and broadcast as weights; no client ever opens a
`.pt` (see *Scope* above). `make run`/`make export` prepare both automatically (they run
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
   `RAW_CHECKPOINT` at it instead. Request access with the [Ark+ checkpoint access form](https://forms.gle/qkoDGXNiKRPTDdCe8).

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

- `JOB_TYPE=evaluation`
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

# Export the complete FLARE job for review (no GPU needed)
make export                    # prepares both checkpoints, then writes ./fl_job/flip_evaluation/

# SimEnv local simulation (requires GPU + data + both checkpoints)
make run                       # prepares both checkpoints (if needed), then runs the simulator via `make sim`
```

To override either checkpoint source, set `RAW_CHECKPOINT` / `FINETUNED_CHECKPOINT` in `.env.app` or pass it on the command line, e.g. `make run FINETUNED_CHECKPOINT=/path/to/custom_finetuned.pt` (see [Checkpoint setup](#checkpoint-setup)).

`make run`/`make sim` score a deterministic, class-balanced subset of `MAX_SAMPLES` (default `128`)
hold-out studies per simulated site (`cap_dataframe` in `app_files/data_utils.py`), so a plain run is a
quick smoke test; `make run MAX_SAMPLES=0` (or empty) scores the full ~470 studies per site. The cap is
simulator-only: only the `LOCAL_DEV` dataframe path reads it, and a deployed job scores whatever cohort
the trust's data-access-api returns. `WORKSPACE` overrides the simulator workspace root.

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

The evaluator returns **aggregate** (cohort-level) metrics only: per-lesion AUROC per model, pairwise DeLong p-values, and Benjamini-Hochberg (BH) FDR-adjusted q-values. The server collects them into `evaluation_results.json`:

- The file is keyed by site, then by the server's validate task (`SRV_<model>`, one per model in `config.json`), then by model.
- Each reply carries every model the site has scored so far. The entry for the site's last task holds every model's AUROCs plus the DeLong tables. An earlier entry holds only the models scored up to then, with no DeLong tables while only one model has been scored.
- The AUROCs are the reply's `metrics`, which the FL server also sends to the hub: one point per trust for each `<MODEL>/AUROC_<LESION>` (e.g. `ARKPLUS_PRETRAINED/AUROC_EFFUSION`), so ten plots for two models and five lesions.
- The DeLong tables go in the reply's `meta[FlipMetaKey.SUPPLEMENTARY_RESULTS]`, which the server merges back under each model in the results file only.

Example:

```json
{
    "site-1": {
        "SRV_arkplus_pretrained": {
            "arkplus_pretrained": {
                "auroc_Effusion": 0.85,
                "...": "..."
            }
        },
        "SRV_arkplus_finetuned": {
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
        }
    },
    "site-2": {
        "...": "..."
    }
}
```

(Values above are **synthetic placeholders** chosen to show the JSON shape — not measured scores.)

### Fields

| Key | Type | Description |
|-----|------|-------------|
| `auroc_<Lesion>` | `float` | Area under the ROC curve for this lesion. Ranges `[0, 1]`; `NaN` if only one class is present in the ground truth. |
| `delong_p_values` | `dict[str, dict[str, float]]` | Pairwise DeLong p-values for each lesion, keyed first by lesion name then by the *other* model name. The diagonal (model vs. self) is hardcoded `1.0` as a sanity check; off-diagonal entries are the two-sided DeLong test. `NaN` if the lesion's cohort has only one class present, or if either class has fewer than 2 members (DeLong's variance estimate is inestimable with a single sample in either class). Only present when ≥ 2 models are configured. |
| `delong_q_values` | `dict[str, dict[str, float]]` | Benjamini-Hochberg FDR-adjusted q-values, same shape and diagonal convention as `delong_p_values`. Corrected **per model pair**, across that pair's lesion-level p-values — each pair is its own independent correction family, never pooled across multiple pairs. `NaN` wherever the corresponding `delong_p_values` entry is `NaN` (excluded from that pair's correction, not folded into the other lesions' ranks). Only present when ≥ 2 models are configured. |

Per-sample (row-level) predictions are deliberately **not** produced or exported: a per-patient list would
leak the exact evaluation cohort size and be linkable to individual patients.

### Reading `delong_q_values`

A q-value is not a rescaled or "corrected" version of that lesion's raw p-value. It is the smallest false-discovery
rate (FDR) at which this lesion's comparison would be called significant, considered jointly with the other lesion
tests for the same model pair.

- `q <= 0.05` means "this finding survives if we accept a 5% FDR across this pair's lesion comparisons".
- It does not mean "a 5% chance of this AUROC gap under the null". That is what the raw p-value alone means,
  without accounting for the other simultaneous lesion tests.
- To decide which lesions survived correction, compare `q` with your chosen significance level, not the raw
  `delong_p_values`.

A q-value is always `>= p` and can differ substantially from it. Several lesions can share the same q-value even
with different raw p-values.

**Why ties happen.** The step-up rule always rejects a contiguous prefix of the sorted ranks. If rank `k` crosses
its own line `p_(k) <= (k/m)*Q`, every smaller rank `1..k-1` is rejected with it, whether or not its own raw value
crosses at that `Q`. So `q_(i) = min` over `j >= i` of `p_(j) * m/j`, computed as a running minimum from the
largest rank inward. Example with `m=3` sorted p-values `0.02, 0.025, 0.03`: the per-rank raw values are
`0.06, 0.0375, 0.03` (not ascending), and the running minimum from the back gives `q = 0.03` for all three. This
is expected, not a bug: a later, weaker-p lesion can set the q-value of an earlier, stronger-p lesion in the same
pair.

**From an FDR level to a p-value cutoff.** Pick a target `Q` for a model pair and take every lesion in that pair
with `q <= Q`. The largest raw p-value among them is the critical cutoff for that `Q`: every `delong_p_values`
entry at or below it is exactly the set that survives at `Q`. Use this to present a table of raw p-values (or AUROC
gaps) with the surviving cells flagged, stating `Q` and the derived cutoff together in the caption, instead of
printing a `q` column.

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
`omni_heads`). Without an explicit pool the heads would emit a **per-location** grid of outputs instead of one
prediction per image, giving mis-shaped predictions and an AUROC failure with
`ValueError: multi_class must be in ('ovo', 'ovr')`.

**Fix.** `forward` (and `generate_embeddings`) apply an explicit global-average-pool over the spatial
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
- BH correction runs separately per model pair, over that pair's lesion-level p-values (5 tests per pair in this tutorial's config). The other rules (≥ 2 models required, `NaN` handling, no pooling across pairs) are in [Fields](#fields).
- Both models are loaded onto the GPU together; if you hit an out-of-memory error, reduce `BATCH_SIZE` in
  `config.json` (it defaults to 1) or evaluate on a smaller GPU-fitting cohort.
