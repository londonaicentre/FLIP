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

# Tutorial datasets

Shared download/derive/enrich tooling for the FL tutorial datasets. Datasets are
**backend-agnostic** — a tutorial's NVFLARE and Flower twins train on the same data — and
**many-to-one** — one dataset serves several tutorials (spleen alone backs segmentation,
evaluation and diffusion) — so their tooling lives here once instead of duplicated per
backend tree. Everything downloads at run time; nothing is committed.

All outputs land under the shared, gitignored [`fl-tutorials/data/`](../) root, so one
download serves both backends' harnesses (the NVFLARE simulator via each tutorial's
`.env.app`, the Flower compose stack via `flower/run-tutorial.sh`).

Invoke the targets through the fl-tutorials root Makefile (which forwards here):

```bash
make -C fl-tutorials download-xray-data
make -C fl-tutorials download-spleen-data              # MSD build (NUM_CASES=<1-41>, default 10)
make -C fl-tutorials download-spleen-data FL_BACKEND=flower   # pre-built FLIP-format tree
make -C fl-tutorials download-arkplus-finetuning-data  # large (~6.3 GB)
make -C fl-tutorials download-arkplus-eval-data        # (~1.6 GB)
make -C fl-tutorials upload-spleen-labels FLIP_PROJECT_ID=<uuid>   # data enrichment
```

| Dataset | Source | Output under `fl-tutorials/data/` | Consumed by |
| --- | --- | --- | --- |
| xray | HF `aicentreflip/flip-fl-base-test-data` | `xrays_mini_300/{accession-resources/, dataframe.csv}` | xray_classification (both backends) |
| spleen | MSD Task09_Spleen (`NUM_CASES`, default 10) | `spleen/{images/, dataframe.csv}` | 3d_spleen_segmentation + evaluation + latent_diffusion_model (**both backends**); enrichment labels |
| spleen checkpoint | HF `aicentreflip/flip-fl-base-test-data` | `model_checkpoints/model.pt` | 3d_spleen_segmentation_evaluation |
| arkplus | HF `aicentreflip/tutorials-arkplus-cxr-classification` | `arkplus/site{1,2}[,_holdoff]/` | the three Ark+ tutorials (NVFLARE) |

The two spleen variants coexist in `data/spleen/` — the FLIP-format download removes only
its own outputs, never an MSD build beside it.

## Per-dataset scripts

[`spleen/`](spleen/) owns the spleen scripts and their uv project (`pyproject.toml` — MONAI,
pandas, natsort; `uv.lock` is gitignored):

- `download_spleen_dataset.py` — fetch MSD spleen cases and reorganise each subject to hold
  image + label.
- `create_spleen_accession_csv.py` — build the `accession_id` dataframe the trainers read in
  LOCAL_DEV.
- `upload_spleen_labels_to_xnat.py` — the data-enrichment step: push `label_*.nii.gz` files
  into a real FLIP project's XNAT (see the
  [spleen tutorial README](../nvflare/image_segmentation/3d_spleen_segmentation/README.md)
  for the full walkthrough, and the repo-root `CLAUDE.md` for its `e2e_smoke` wiring). Runs
  against the in-tree `flip-utils`, not `spleen/`'s env.
- `download_spleen_checkpoint.py` — fetch the evaluation-tutorial checkpoint from Hugging
  Face. A pure Hugging Face fetch, so like the xray/arkplus scripts it runs via
  `uv run --no-project --with huggingface_hub`, not in `spleen/`'s env. It used to also
  download a fixed 6-case "FLIP-format" spleen tree for the Flower tutorials; both backends
  now read the one MSD build (FLIP#1158), which honours `NUM_CASES`.

[`xrays_mini_300/`](xrays_mini_300/) owns the single x-ray script — no dedicated uv project,
it runs via `uv run --no-project --with huggingface_hub`, the same way `upload-spleen-labels`
runs against `flip-utils` without adopting `spleen/`'s env:

- `download_xrays_dataset.py` — fetch the Hugging Face snapshot and normalise it into
  `accession-resources/` + `dataframe.csv`.

[`arkplus/`](arkplus/) owns the single arkplus script — no dedicated uv project, it runs via
`uv run --no-project --with huggingface_hub`, the same way `upload-spleen-labels` runs
against `flip-utils` without adopting `spleen/`'s env:

- `download_arkplus_dataset.py` — fetch the given site folders (TRAIN or HOLD-OUT) from
  Hugging Face and normalise each into `accession-resources/` +
  `sample_get_dataframe_response.csv`. Parameterised by `--sites`, so one script backs both
  `download-arkplus-finetuning-data` and `download-arkplus-eval-data`.
