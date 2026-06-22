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

To update the dependencies, edit the `pyproject.toml` file in the root folder and then run the following command:

```bash
uv sync [--no-dev]
```
