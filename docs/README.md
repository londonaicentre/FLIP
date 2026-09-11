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

# Documentation

Uses [sphinx-autoapi](https://sphinx-autoapi.readthedocs.io/en/latest/) to generate API documentation from docstrings in the codebase.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) — installs the pinned Sphinx toolchain from `pyproject.toml` / `uv.lock`.
- graphviz (`dot` on PATH, e.g. `apt-get install graphviz`) — `source/conf.py` renders the Central Hub AWS
  diagrams from `../deploy/providers/AWS/architecture/central_hub.py` at build time and fails without it.
  ReadTheDocs installs it through `build.apt_packages`; for a text-only build on a host without graphviz,
  set `FLIP_DOCS_SKIP_DIAGRAMS=1` (the two Central Hub deployment pages then warn about their missing images).

To generate the documentation, run (from the repository root):

```bash
make -C docs clean
make -C docs docs
```

Or, from inside the `docs/` directory:

```bash
cd docs
make clean
make docs
```
