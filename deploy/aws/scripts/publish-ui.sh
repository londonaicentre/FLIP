#!/usr/bin/env bash
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

# Publish a built flip-ui to its bucket: the app (`npm run build:deploy`, dist/) to the UI bucket, or the
# public demo (`npm run build:demo`, dist-demo/, FLIP#2012) to its own. `make deploy-ui`, `make deploy-demo`
# and the CI publish (.github/workflows/publish_ui.yml, FLIP#1186) all run it, so the cache-control split
# lives in one place:
#
#   * hashed bundles are content-addressed, so they are cached for a year;
#   * index.html and js/window.js reference those bundles and must never be cached;
#   * --delete removes the previous build, except under demo/: until the old /demo/ URLs redirect to the
#     demo's own host, the UI bucket still holds the last demo published there (FLIP#2012).
#
# Nothing is invalidated: the networking account's edge serves the bucket (FLIP#749), and the no-cache
# objects are re-fetched anyway.
#
# Usage: publish-ui.sh <dist dir> <bucket>
# The aws CLI is called with the caller's credentials.

set -euo pipefail

usage="Usage: publish-ui.sh <dist dir> <bucket>"
dist="${1:?${usage}}"
bucket="${2:?${usage}}"

fail() {
    echo "❌ $1" >&2
    exit 1
}

entries=(index.html js/window.js)

# Without these the sync would publish a site whose entry point is the previous build's, or none.
for entry in "${entries[@]}"; do
    [[ -f "${dist}/${entry}" ]] || fail "${dist}/${entry} is missing: run the build first."
done

echo "📦 Syncing ${dist} to s3://${bucket}/ ..."
aws s3 sync "${dist}/" "s3://${bucket}/" \
    --delete \
    --cache-control "public, max-age=31536000, immutable" \
    --exclude "index.html" --exclude "js/window.js" --exclude "demo/*"
for entry in "${entries[@]}"; do
    case "${entry}" in
        *.html) type="text/html; charset=utf-8" ;;
        *) type="application/javascript; charset=utf-8" ;;
    esac
    aws s3 cp "${dist}/${entry}" "s3://${bucket}/${entry}" \
        --cache-control "no-cache, no-store, must-revalidate" \
        --content-type "${type}"
done

echo "✅ ${dist} published to s3://${bucket}/."
