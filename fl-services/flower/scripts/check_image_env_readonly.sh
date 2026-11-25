#!/usr/bin/env bash
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
# Run check_image_env_readonly.py inside each given Flower FL image, once per runtime uid, and fail
# if any check fails (FLIP#1573). CI runs this after the Flower images are built and before they are
# pushed; `make -C fl-services/flower check-env-readonly` runs it over a local build.
#
# Usage:
#   check_image_env_readonly.sh <image> [<image> ...]
#   PROBE_UIDS="49999 1234" check_image_env_readonly.sh <image>
#
# Which uids and why: 49999 is the image user `app` (flwr/base's own uid, what Helm, SPCS and the
# compose stacks run by default), and 1234 stands in for an arbitrary kit FLIP_UID (FLIP#1731), which
# owns nothing in the image. Both must be unable to write the import path. The probe refuses to run
# as root, so a `--user` that resolves to uid 0 fails rather than passing vacuously.
#
# Only images whose runtime executes app code need this: fl-base and the superlink/supernode images
# built from it. The fleet proxy is nginx with no interpreter, and the FL API image runs FLIP's own
# service code only (no ServerApp/ClientApp), so neither is in the CI list.

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $(basename "$0") <image> [<image> ...]" >&2
  exit 2
fi

PROBE_UIDS="${PROBE_UIDS:-49999 1234}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROBE="${SCRIPT_DIR}/check_image_env_readonly.py"
[[ -f "$PROBE" ]] || { echo "❌ probe not found at $PROBE" >&2; exit 2; }

failures=0
runs=0

for image in "$@"; do
  for uid in $PROBE_UIDS; do
    runs=$((runs + 1))
    echo "──────── ${image} as uid ${uid}"
    # The probe is bind-mounted rather than baked in: the images ship no test code, and the same
    # probe then runs unchanged against an older published image.
    if docker run --rm \
        --user "${uid}:${uid}" \
        --network none \
        --entrypoint python \
        -v "${PROBE}:/check_image_env_readonly.py:ro" \
        "${image}" /check_image_env_readonly.py --label "${image} uid=${uid}"; then
      echo "✅ ${image} (uid ${uid}): import path is read-only to the runtime user"
    else
      echo "❌ ${image} (uid ${uid}): the runtime user can write the import path"
      failures=$((failures + 1))
    fi
  done
done

echo
if [[ $failures -gt 0 ]]; then
  echo "❌ FL image environment probe: ${failures} of ${runs} image/uid combinations failed"
  exit 1
fi
echo "✅ FL image environment probe: all ${runs} image/uid combinations passed"
