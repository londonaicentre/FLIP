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
| `COPY --from=weights` | TotalSegmentator's weights for the tasks in the `TOTALSEG_TASKS` build argument (default `total`), under `/opt/totalsegmentator` (`TOTALSEG_HOME_DIR`) | about 1.4 GB |

The weights are downloaded in a build stage of their own and checked against the `TOTALSEG_WEIGHTS_SHA256`
build argument, so a changed download fails the build. Their timestamps are fixed, so the same weights give
the same layer in every build and a trust pulls it once. To change `TOTALSEG_TASKS`, change the digest with
it: a failed check prints the one it found.

`/opt/totalsegmentator` is owned by root and read-only to a job. TotalSegmentator's usage statistics are off
in its `config.json`, and a job cannot turn them on.

The weights have their own licences: `total` is open, and some other tasks need a licence from the authors.
Check a task's licence before adding it to `TOTALSEG_TASKS`.

## Running it at a trust

A trust chooses its client image in its kit file:

```bash
FL_ENVIRONMENT=segmentation
```

`deploy/fl_backend.mk` derives the image name from it, so `up-trust`, `restart-trust` and `upgrade-trust` all
run `flare-fl-client-segmentation`, and `upgrade-trust` checks that image is published at the target tag
before it changes anything. An unknown name, or one given with `FL_BACKEND=flower`, is refused. A job cannot
choose its image: a trust runs one client image. The Helm chart does not read the key yet.

## Build and run locally

From the repo root, after `make build-fl`:

```bash
make -C fl-services/nvflare build-segmentation
make up DOCKER_FL_REGISTRY= DOCKER_FL_TAG=dev FL_ENVIRONMENT=segmentation
```

`build-segmentation` takes `LOCAL_DEV` as `build-fl` does; give both the same value. `FL_ENVIRONMENT` on the
command line, or in `.env.development`, selects the image for every trust of the stack.

## Using it in a job

```python
from totalsegmentator.python_api import totalsegmentator

totalsegmentator(ct_path, output_path, task="total", ml=True, quiet=True)
```

To add a package to this environment, add it to the `segmentation` extra, refresh the lockfile with
`make lock`, and rebuild.
