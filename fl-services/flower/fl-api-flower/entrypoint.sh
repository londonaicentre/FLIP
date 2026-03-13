# Copyright (c) 2026 Flower Labs GmbH
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

#!/usr/bin/env bash
set -euo pipefail

mkdir -p /home/app/.flwr
if [ -n "${SUPERLINK_ROOT_CERTIFICATES:-}" ]; then
cat >/home/app/.flwr/config.toml <<EOF
[superlink.local]
address = "${SUPERLINK_ADDRESS:-superlink:9093}"
root-certificates = "${SUPERLINK_ROOT_CERTIFICATES}"
EOF
else
cat >/home/app/.flwr/config.toml <<EOF
[superlink.local]
address = "${SUPERLINK_ADDRESS:-superlink:9093}"
insecure = true
EOF
fi

exec uv run python -m uvicorn fl_api.app:app --host 0.0.0.0 --port 8000 --reload --reload-dir /app/fl_api
