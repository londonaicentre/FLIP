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

# Publish a built flip-ui (`npm run build:deploy`, which writes dist/js/window.js) to the UI bucket.
# `make deploy-ui` and the CI publish (.github/workflows/publish_ui.yml, FLIP#1186) both run it, so the
# cache-control split lives in one place:
#
#   * hashed bundles are content-addressed, so they are cached for a year;
#   * index.html and js/window.js reference those bundles and must never be cached;
#   * --delete removes the previous build, but never ark_demo/, the Ark+ demo published into the same
#     bucket by `make deploy-ark-demo`.
#
# Pass a CloudFront distribution id only where the FLIP root owns one. On LZA the networking account's
# edge serves the bucket and nothing is invalidated (FLIP#749): the no-cache objects are re-fetched anyway.
#
# Usage: publish-ui.sh <dist dir> <bucket> [cloudfront distribution id]
# The aws CLI is called with the caller's credentials.

set -euo pipefail

usage="Usage: publish-ui.sh <dist dir> <bucket> [cloudfront distribution id]"
dist="${1:?${usage}}"
bucket="${2:?${usage}}"
distribution="${3:-}"

fail() {
    echo "❌ $1" >&2
    exit 1
}

# Without these the sync would publish a site whose entry point is the previous build's, or none.
for entry in index.html js/window.js; do
    [[ -f "${dist}/${entry}" ]] || fail "${dist}/${entry} is missing: run npm run build:deploy first."
done

echo "📦 Syncing ${dist} to s3://${bucket}/ ..."
aws s3 sync "${dist}/" "s3://${bucket}/" \
    --delete \
    --cache-control "public, max-age=31536000, immutable" \
    --exclude "index.html" --exclude "js/window.js" --exclude "ark_demo/*"
aws s3 cp "${dist}/index.html" "s3://${bucket}/index.html" \
    --cache-control "no-cache, no-store, must-revalidate" \
    --content-type "text/html; charset=utf-8"
aws s3 cp "${dist}/js/window.js" "s3://${bucket}/js/window.js" \
    --cache-control "no-cache, no-store, must-revalidate" \
    --content-type "application/javascript; charset=utf-8"

if [[ -n "${distribution}" ]]; then
    echo "🧹 Invalidating CloudFront distribution ${distribution}..."
    aws cloudfront create-invalidation --distribution-id "${distribution}" --paths "/*" >/dev/null
else
    echo "🧹 No CloudFront distribution given: skipping the invalidation."
fi
echo "✅ flip-ui published to s3://${bucket}/."
