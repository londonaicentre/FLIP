<!--
    Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

The FL base image should install the python packages required by the base application, as well as the python packages
required by the user-uploaded application. At the moment, the user can't specify additional packages to install, but
this should be added in the future to support custom user environments (see [issue #421](https://github.com/londonaicentre/flip/issues/421)).

## GPU resource management

The resources allocated to the FL client are configured in a resources.json file.

They can be changed using environment variables when starting the FL client container.
