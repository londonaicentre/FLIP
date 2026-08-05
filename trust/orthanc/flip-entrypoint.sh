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
# and separately refuse blank usernames/passwords — empty or whitespace-only,
# since Orthanc accepts a single space as a credential — so a valid pair cannot
# mask a blank-credential user elsewhere in the map (the greps are a heuristic
# on the JSON text, not a parser). Also re-assert ORTHANC__AUTHENTICATION_ENABLED
# here: the Dockerfile sets it and nothing in the platform overrides it, but a
# runtime check makes "this image cannot boot unauthenticated" enforced rather
# than conventional. Point the operator at the credential source for their
# deployment mode.
set -eu

fail() {
    echo "REFUSING TO START: $1" >&2
    echo "Set ORTHANC__REGISTERED_USERS to a non-empty JSON user map" >&2
    echo "(compose: ORTHANC_USERNAME/ORTHANC_PASSWORD in the trust kit file" >&2
    echo "trust/.env.<CODE>.<env>; k8s: the orthanc-registered-users secret key)." >&2
    exit 1
}

fail_auth() {
    echo "REFUSING TO START: $1" >&2
    echo "ORTHANC__AUTHENTICATION_ENABLED must be \"true\" — this image always" >&2
    echo "enforces HTTP basic auth; unset it to take the image default." >&2
    exit 1
}

# Only an unset value falls back to the image default — an explicitly empty
# value is a misconfiguration and must refuse, hence `-` rather than `:-`.
auth="${ORTHANC__AUTHENTICATION_ENABLED-true}"
if [ "$auth" != "true" ]; then
    fail_auth "ORTHANC__AUTHENTICATION_ENABLED is \"$auth\", not \"true\""
fi

users="${ORTHANC__REGISTERED_USERS:-}"
if [ -z "$users" ]; then
    fail "ORTHANC__REGISTERED_USERS is unset or empty"
fi
if printf '%s' "$users" | grep -q '"[[:space:]]*"[[:space:]]*:'; then
    fail "ORTHANC__REGISTERED_USERS contains a blank username"
fi
if printf '%s' "$users" | grep -q ':[[:space:]]*"[[:space:]]*"'; then
    fail "ORTHANC__REGISTERED_USERS contains a blank password"
fi
if ! printf '%s' "$users" | grep -q '"[^"][^"]*"[[:space:]]*:[[:space:]]*"[^"][^"]*"'; then
    fail "ORTHANC__REGISTERED_USERS contains no username/password pair"
fi

exec /docker-entrypoint.sh "$@"
