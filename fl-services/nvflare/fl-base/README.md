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

# Base FL image

FL base will be used as the base image for both FL client and FL server images. It contains all the common dependencies
required for both the FL client and FL server to run.

This image is built from [`flip-utils/pyproject.toml`](../../../flip-utils/pyproject.toml) and its
`uv.lock` — the Dockerfile copies both in and runs `uv sync --frozen --no-cache --extra full`. To
update the dependencies, edit `flip-utils/pyproject.toml`, refresh the lockfile with `make lock`
(or `uv lock` from `flip-utils/`), then rebuild the FL images with `make build-fl`.
