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

# Client API inference

## Overview

This is the FLARE **Client API** inference job type (`JOB_TYPE=inference`). Each site runs a model
on its own data and keeps the output there, for example a predicted mask stored on a scan in the
site's XNAT. There are no reference labels and nothing is scored.

It is the [`evaluation`](../evaluation/README.md) pipeline with two differences:

- The user's script is `inference.py`, and the app needs no `models.py`.
- A `models` entry in `config.json` may be empty. That names a model whose weights are already at
  the sites, in the FL image, so no checkpoint is uploaded or sent:

  ```json
  { "job_type": "inference", "models": { "totalsegmentator": {} } }
  ```

  An entry with `checkpoint` and `path` works as it does in an evaluation.

The script answers the `validate` task through the FLARE Client API, as an evaluator does, and must
reply with metrics: counts, such as how many studies were predicted. The Central Hub shows them per
site, and a reply without metrics counts as a failed task. Write outputs back to the site with
`flip.add_resource`; nothing but the metrics leaves it.

The base configs (`app/config/config_fed_server.json`, `app/config/config_fed_client.json`) and
`meta.json` are **recipe-generated** from `flip.nvflare.recipes.FlipInferenceRecipe`. Do not
hand-edit them: regenerate them with [`recipe.py`](recipe.py) after a recipe change.
