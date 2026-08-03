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
#
# RELEASE_VERSION stamps the register's vintage so a mutable /ark_demo/ URL
# still tells a visitor (or a script probing the header) which snapshot it is
# serving. The capture date is grepped out of mocks/demo/ark-plus-register.ts
# rather than duplicated here, so it cannot drift from that file's
# DEMO_CAPTURE_DATE — the same constant src/demo/DemoBanner.vue renders. Run
# from flip-ui/ (the npm postbuild:demo hook's cwd), so both paths below are
# relative to that.

CAPTURE_DATE=$(sed -n 's/^export const DEMO_CAPTURE_DATE = "\(.*\)";$/\1/p' mocks/demo/ark-plus-register.ts)
CAPTURE_DATE=${CAPTURE_DATE:-unknown}
SHORT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)

cat <<EOF
window.AWS_BASE_URL = "/api";
window.AWS_REGION = "eu-west-2";
window.AWS_USER_POOL_ID = "demo-user-pool";
window.AWS_CLIENT_ID = "demo-client";
window.BLACKLISTED_MODEL_FILES = "";
window.RELEASE_VERSION = "ark-demo/${CAPTURE_DATE}+${SHORT_SHA}";
EOF
