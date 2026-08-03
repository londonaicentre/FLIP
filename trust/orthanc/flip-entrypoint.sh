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
#

# FLIP-PT-091: fail closed — never boot an Orthanc that would accept
# unauthenticated HTTP. Without ORTHANC__REGISTERED_USERS — or with a userless
# map like {} — the base image falls back to the well-known orthanc/orthanc
# default user, and a kit file missing ORTHANC_USERNAME/ORTHANC_PASSWORD
# renders the compose user map as {"": ""}, which basic auth accepts with
# empty credentials. Require at least one non-empty "user": "password" pair,
# and separately refuse empty usernames/passwords so a valid pair cannot mask
# an empty-credential user elsewhere in the map (the greps are a heuristic on
# the JSON text, not a parser). Point the operator at the credential source
# for their deployment mode.
set -eu

fail() {
    echo "REFUSING TO START: $1" >&2
    echo "Set ORTHANC__REGISTERED_USERS to a non-empty JSON user map" >&2
    echo "(compose: ORTHANC_USERNAME/ORTHANC_PASSWORD in the trust kit file" >&2
    echo "trust/.env.<CODE>.<env>; k8s: the orthanc-registered-users secret key)." >&2
    exit 1
}

users="${ORTHANC__REGISTERED_USERS:-}"
if [ -z "$users" ]; then
    fail "ORTHANC__REGISTERED_USERS is unset or empty"
fi
if printf '%s' "$users" | grep -q '""[[:space:]]*:'; then
    fail "ORTHANC__REGISTERED_USERS contains an empty username"
fi
if printf '%s' "$users" | grep -q ':[[:space:]]*""'; then
    fail "ORTHANC__REGISTERED_USERS contains an empty password"
fi
if ! printf '%s' "$users" | grep -q '"[^"][^"]*"[[:space:]]*:[[:space:]]*"[^"][^"]*"'; then
    fail "ORTHANC__REGISTERED_USERS contains no username/password pair"
fi

exec /docker-entrypoint.sh "$@"
