#!/usr/bin/env sh
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

set -e

DEBUG_CMD="-Xfrozen_modules=off -m debugpy --listen 0.0.0.0:5679 --wait-for-client"
API_CMD="-m uvicorn fl_api.app:app --host 0.0.0.0 --port 8000 --reload --reload-dir ./fl_api"


if [ "$DEBUG" = "true" ]; then
    echo "🚨 Starting API in debug mode... "
    exec uv run python $DEBUG_CMD $API_CMD
else
    echo "🚢 Starting API in normal mode... "
    exec uv run python $API_CMD
fi