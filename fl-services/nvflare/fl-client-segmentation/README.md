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

# Federated Learning Client: segmentation environment

> See also: [Federated Learning Client](../fl-client/README.md) and the [FLARE services overview](../README.md).

`flare-fl-client-segmentation` is the [FL client image](../fl-client/README.md) with one more FL environment
installed (FLIP#32): the `segmentation` extra of [`flip-utils`](../../../flip-utils/pyproject.toml), which is
[TotalSegmentator](https://github.com/wasserth/TotalSegmentator) with the nnU-Net and SimpleITK it runs on.
A job cannot add dependencies or download at a trust, so a job that needs them runs at a trust whose client
is this image.

It is built `FROM` the client image and adds two layers, so what a trust has reviewed in the client image is
unchanged:

| Layer | Holds | Size |
|-------|-------|------|
| `uv sync --frozen ... --extra segmentation` | The packages `flip-utils/uv.lock` adds for the extra. No package of the client image changes version | about 1.1 GB |
| `totalseg_download_weights` | TotalSegmentator's weights for the tasks in the `TOTALSEG_TASKS` build argument (default `total`), under `/opt/totalsegmentator` (`TOTALSEG_HOME_DIR`) | about 1.4 GB |

TotalSegmentator's usage statistics are turned off in the image. Its weights have their own licences: `total`
is open, and some other tasks need a licence from the authors. Check a task's licence before adding it to
`TOTALSEG_TASKS`.

## Build and run locally

From the repo root, after `make build-fl`:

```bash
make -C fl-services/nvflare build-segmentation
make up DOCKER_FL_REGISTRY= DOCKER_FL_TAG=dev DOCKER_FL_CLIENT_NAME=flare-fl-client-segmentation
```

`DOCKER_FL_CLIENT_NAME` on the command line selects the image for every trust of the stack. A job cannot
choose its image: a trust runs one client image.

## Using it in a job

```python
from totalsegmentator.python_api import totalsegmentator

totalsegmentator(ct_path, output_path, task="total", ml=True, quiet=True)
```

To add a package to this environment, add it to the `segmentation` extra, refresh the lockfile with
`make lock`, and rebuild.
