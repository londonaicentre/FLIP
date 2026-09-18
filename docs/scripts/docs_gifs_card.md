---
license: apache-2.0
pretty_name: FLIP documentation GIFs
viewer: false
tags:
  - flip
  - federated-learning
  - documentation
---

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

# FLIP documentation GIFs

The animated walkthroughs embedded in the [FLIP](https://github.com/londonaicentre/FLIP) user guides on
[ReadTheDocs](https://londonaicentreflip.readthedocs.io/en/latest/). They are recorded by Cypress from the
FLIP UI against a mocked backend (`flip-ui/test/cypress/docs/`), converted with ffmpeg, and published here by
`.github/workflows/regenerate_docs_gifs.yml` so the recordings never enter the git repository
([FLIP#1236](https://github.com/londonaicentre/FLIP/issues/1236)).

## Layout

One copy of every file, at an unversioned path:
