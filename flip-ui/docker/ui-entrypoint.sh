#!/bin/sh

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

set -eu

cd /app

if [ -f package-lock.json ]; then
    mkdir -p node_modules

    install_state_file="node_modules/.install-state"
    lock_hash="$(sha256sum package-lock.json | awk '{print $1}')"
    runtime_id="$(node -p "process.version + '-' + process.platform + '-' + process.arch")"
    expected_state="${runtime_id}-${lock_hash}"
    installed_state="$(cat "$install_state_file" 2>/dev/null || true)"

    needs_install=0
    if [ "$expected_state" != "$installed_state" ]; then
        needs_install=1
    fi

    if [ "$needs_install" -eq 0 ] && ! node -e "import('vite-plugin-vue-layouts-next').then(()=>process.exit(0)).catch(()=>process.exit(1))" >/dev/null 2>&1; then
        needs_install=1
    fi

    if [ "$needs_install" -eq 0 ] && ! node -e "import('rolldown').then(()=>process.exit(0)).catch(()=>process.exit(1))" >/dev/null 2>&1; then
        needs_install=1
    fi

    if [ "$needs_install" -eq 1 ]; then
        npm install --include=optional
        echo "$expected_state" > "$install_state_file"
    fi
fi

exec "$@"
