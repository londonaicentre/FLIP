#!/bin/sh
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
# Emits the runtime window.* config for the public Ark+ demo build.
#
# Unlike scripts/generate-window-js.sh, this reads NO environment and points at
# NO real backend. AWS_BASE_URL is "/api" — the prefix the in-browser demo
# Mirage server (mocks/demo-server.ts) answers on — and the Cognito values are
# inert placeholders (the demo bypasses login entirely). This makes the classic
# <script src="…/js/window.js"> in index.html a no-op that only exists so the
# bundle carries valid config before its ES modules evaluate.

cat <<'EOF'
window.AWS_BASE_URL = "/api";
window.AWS_REGION = "eu-west-2";
window.AWS_USER_POOL_ID = "demo-user-pool";
window.AWS_CLIENT_ID = "demo-client";
window.BLACKLISTED_MODEL_FILES = "";
window.RELEASE_VERSION = "ark-demo";
EOF
