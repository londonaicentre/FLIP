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

> See also: [Federated Learning Server](../fl-server/README.md) and the [FLARE services overview](../README.md).

The client runs on the [FL base image](../fl-base/README.md), which installs the Python packages needed by the
base application and by user-uploaded applications. These dependencies are baked into the image: a job cannot add
its own dependencies on the FLARE backend. The Flower SuperNodes do the same; see the [Flower services README](../../flower/README.md).

## GPU resource management

The resources allocated to the FL client are configured in a resources.json file.

They can be changed using environment variables when starting the FL client container.
