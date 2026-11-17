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
# Mirror the third-party images a trust node runs into ghcr.io/londonaicentre/mirror/ (FLIP#1721).
# Reads trust/deploy/mirror-images.manifest (`<upstream> <index digest>` per line) and, for each
# line, copies <upstream repo>@<digest> to <mirror>/<name>:<tag> with `docker buildx imagetools
# create` — a registry-to-registry copy of the index as it is, so the mirror carries upstream's
# digest. A mirror already at that digest is left alone.
#
#   scripts/mirror_images.sh           copy what is missing (needs a GHCR login with write:packages)
#   scripts/mirror_images.sh --check   push nothing; fail on a digest upstream no longer serves, or a
#                                      mirror that is missing or differs
set -euo pipefail

MANIFEST=${MANIFEST:-trust/deploy/mirror-images.manifest}
MIRROR=${MIRROR:-ghcr.io/londonaicentre/mirror}
check=0
[[ "${1:-}" == "--check" ]] && check=1

# The digest of what the reference resolves to (an index's digest is the sha256 of its bytes); empty
# when it does not resolve.
digest_of() {
  local sum
  sum=$(docker buildx imagetools inspect "$1" --raw 2>/dev/null | shasum -a 256; exit "${PIPESTATUS[0]}") || return 0
  printf 'sha256:%s' "${sum%% *}"
}

status=0
while read -r upstream digest _; do
  [[ -z "$upstream" || "$upstream" == \#* ]] && continue
  repo=${upstream%:*}
  tag=${upstream##*:}
  mirror="$MIRROR/${repo#*/}:$tag"

  if [[ "$(digest_of "$repo@$digest")" != "$digest" ]]; then
    echo "FAIL $upstream: upstream does not serve $digest"
    status=1
    continue
  fi
  have=$(digest_of "$mirror")
  if [[ "$have" == "$digest" ]]; then
    echo "ok     $mirror"
    continue
  fi
  if (( check )); then
    echo "FAIL $mirror: ${have:-missing}, manifest says $digest — run \`make mirror-images\` or dispatch mirror_images.yml on this branch"
    status=1
    continue
  fi
  docker buildx imagetools create --tag "$mirror" "$repo@$digest"
  have=$(digest_of "$mirror")
  if [[ "$have" == "$digest" ]]; then
    echo "pushed $mirror"
  else
    echo "FAIL $mirror: pushed ${have:-nothing}, want $digest"
    status=1
  fi
done < "$MANIFEST"
exit "$status"
