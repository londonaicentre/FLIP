# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

LOCAL_ROUNDS = "LOCAL_ROUNDS"
GLOBAL_ROUNDS = "GLOBAL_ROUNDS"
CONFIG = "config.json"
CONFIG_FED_CLIENT = "config_fed_client.json"
CONFIG_FED_SERVER = "config_fed_server.json"
META = "meta.json"
ENVIRONMENT = "environment.json"
MODELS = "models.py"

# Server-only evaluation checkpoints: the hub bundler diverts them to this S3 prefix, and the
# FL API stages them on the shared checkpoint volume (SERVER_CHECKPOINT_ROOT) for the fl-server
# to load from disk. They are never placed in the NVFLARE job, so they are never deployed to
# clients — mirroring the Flower backend, which keeps the checkpoint server-side.
SERVER_CHECKPOINTS_PREFIX = "server_checkpoints"
SERVER_CHECKPOINT_ROOT_ENV = "SERVER_CHECKPOINT_ROOT"
SERVER_CHECKPOINT_ROOT_DEFAULT = "/app/server-checkpoints"
