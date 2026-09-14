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

# arkplus

Chest X-ray splits backing the three Ark+ tutorials (NVFLARE), sourced from the HF dataset
`aicentreflip/tutorials-arkplus-cxr-classification`: the TRAIN splits (`site1`, `site2`) back
`arkplus_fine_tuning`; the HOLD-OUT splits (`site1_holdoff`, `site2_holdoff`) back the two
evaluation tutorials.

No dedicated uv project — `download_arkplus_dataset.py`'s only dependency is
`huggingface_hub`, so it runs via `uv run --no-project --with huggingface_hub`, the same way
`upload-spleen-labels` runs against `flip-utils` without adopting `spleen/`'s env.

- `download_arkplus_dataset.py` — fetch the given site folders from Hugging Face and
  normalise each into `<site>/{accession-resources/, sample_get_dataframe_response.csv}`.
  One script, parameterised by `--sites`, backs both Makefile targets below; only the TRAIN
  call passes `--write-marker` (`arkplus_fine_tuning/Makefile`'s `reproduce-overhead` checks
  that marker — not just directory existence — before skipping a re-download).

Invoke via the fl-tutorials root Makefile (see [`../README.md`](../README.md)):

```bash
make -C fl-tutorials download-arkplus-finetuning-data  # large (~6.3 GB)
make -C fl-tutorials download-arkplus-eval-data        # (~1.6 GB)
```
