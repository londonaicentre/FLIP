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

Sphinx source for the FLIP documentation published on Read the Docs. The Python API pages are generated from docstrings with [sphinx-autoapi](https://sphinx-autoapi.readthedocs.io/en/latest/).

## Prerequisites

- [uv](https://docs.astral.sh/uv/) — installs the pinned Sphinx toolchain from `pyproject.toml` / `uv.lock`.
- graphviz (`dot` on PATH, e.g. `apt-get install graphviz`). At build time `source/conf.py` renders the Central Hub
  AWS diagrams from `../deploy/providers/AWS/architecture/central_hub.py`, and the build fails without graphviz.
  Read the Docs installs it through `build.apt_packages`.
- Network access to `huggingface.co` and `*.hf.co`. The user-guide GIFs are not tracked in git (FLIP#1236).
  `source/conf.py` fetches them from the public dataset `aicentreflip/docs-gifs` into the gitignored
  `source/assets/generated/gifs/` and verifies every file. The build fails if it cannot fetch them.
  - A normal build takes the newest recording.
  - A release build takes that release's own set (FLIP#1547).
  - A repeat build downloads only what changed.

To build without graphviz or network access, see [Text-only build](#text-only-build).

## Build

From the repository root:

```bash
make -C docs clean
make -C docs docs
```

From inside `docs/`, run `make clean` and then `make docs`.

### Text-only build

On a host without graphviz or network access, skip the diagrams, the GIF fetch, or both:

```bash
FLIP_DOCS_SKIP_DIAGRAMS=1 FLIP_DOCS_SKIP_GIF_FETCH=1 make -C docs docs
```

- `FLIP_DOCS_SKIP_DIAGRAMS=1`: the two Central Hub deployment pages then warn about their missing images.
- `FLIP_DOCS_SKIP_GIF_FETCH=1`: the user-guide pages then warn about their missing images.

## Tests

```bash
make -C docs test
```

Runs the GIF fetcher, publisher and release-tagger suites.
