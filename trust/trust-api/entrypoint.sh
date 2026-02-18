#!/bin/sh
#
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


# Receive the DEBUG from the environment variable
echo "🚀 Starting trust-api entrypoint script..."
echo "🐛 Debug mode: $DEBUG on port ${DEBUG_PORT}"

FAST_API_CMD="-m fastapi dev trust_api/main.py --host 0.0.0.0 --port 8000 --reload"
DEBUG_CMD="-Xfrozen_modules=off -m debugpy --listen 0.0.0.0:${DEBUG_PORT} --wait-for-client"

if [ "$DEBUG" = "true" ]; then
    echo "🚨 Starting API in debug mode... 🐛"
    echo "🚨 Running command: uv run python ${DEBUG_CMD} ${FAST_API_CMD}"
    exec uv run python ${DEBUG_CMD} ${FAST_API_CMD}
else
    echo "🚢 Starting API in normal mode... "
    echo "🚢 Running command: uv run python ${FAST_API_CMD}"
    exec uv run python ${FAST_API_CMD}
fi