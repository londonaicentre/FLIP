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

# xrays_mini_300

Reference x-ray dataset backing the `xray_classification` tutorial (both backends). Sourced
from the `xrays_mini_300/` subtree of the HF dataset `aicentreflip/flip-fl-base-test-data`.

No dedicated uv project — `download_xrays_dataset.py`'s only dependency is `huggingface_hub`,
so it runs via `uv run --no-project --with huggingface_hub`, the same way
`upload-spleen-labels` runs against `flip-utils` without adopting `spleen/`'s env.

- `download_xrays_dataset.py` — fetch the Hugging Face snapshot and normalise it into
  `accession-resources/` + `dataframe.csv` under the shared `fl-tutorials/data/xrays_mini_300/`.

Invoke via the fl-tutorials root Makefile (see [`../README.md`](../README.md)):

```bash
make -C fl-tutorials download-xray-data
```
