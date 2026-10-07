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

# Federated Learning Client

> See also the README under [fl-server/README.md](../fl-server/README.md)

The FL base image installs the python packages required by the base application and the user-uploaded application.
The NVFLARE client dependencies come pre-baked into the image; users cannot add per-job dependencies on the NVFLARE
backend. On the Flower backend, dependencies are installed per run by `uv sync` (SuperNodes opt in via
`--allow-runtime-dependency-installation`) — see `fl-services/flower/README.md`.

## GPU resource management

The resources allocated to the FL client are configured in a resources.json file.

They can be changed using environment variables when starting the FL client container.
