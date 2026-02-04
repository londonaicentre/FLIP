#!/bin/sh
#
# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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
DEBUG=${DEBUG:-false}

echo "🚀 Starting flip-api entrypoint script..."
echo "🐛 Debug mode: $DEBUG"

fast_api_cmd="-m fastapi dev ./src/flip_api/main.py --host 0.0.0.0 --port 8000 --reload"
debug_cmd="-Xfrozen_modules=off -m debugpy --listen 0.0.0.0:5678 --wait-for-client"
seed_cmd="uv run python src/flip_api/db/seed/seed_essential_data.py"

echo fs.inotify.max_user_watches=524288 | tee -a /proc/sys/fs/inotify/max_user_watches

# Ping the Postgres database to check if it's up, no login is needed to this check
>&2 echo "Waiting for Postgres to be available..."
until pg_isready -h $DB_HOST; do
  >&2 echo "."
  sleep 1
done

>&2 echo "Postgres is up - executing seeding command"
${seed_cmd}

# Check if seeding was successful
if [ $? -eq 0 ]; then
    >&2 echo "Seeding database complete - starting FastAPI server"
    if [ "$DEBUG" = "true" ]; then
        echo "🚨 Starting API in debug mode... 🐛"
        exec uv run python ${debug_cmd} ${fast_api_cmd}
    else
        echo "🚢 Starting API in normal mode... "
        exec uv run python ${fast_api_cmd}
    fi
else
    >&2 echo "Seeding database failed - exiting"
    exit 1
fi
