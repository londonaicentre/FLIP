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
# Fail fast when uv is too old to understand the dependency cooldown.
#
# Every pyproject.toml sets `[tool.uv] exclude-newer = "3 days"` (CONTRIBUTING.md
# "Dependency cooldown"). uv only learned that RELATIVE duration form in 0.10.0.
# Older uv cannot parse the value, and its failure mode is quiet and damaging:
# it emits a *warning*, discards the whole [tool.uv] table, then resolves with NO
# cooldown at all and rewrites uv.lock.
#
#   warning: Failed to parse `pyproject.toml` during settings discovery:
#     TOML parse error at line 52, column 17
#     failed to parse year in date "3 days"
#   Ignoring existing lockfile due to removal of timestamp cutoff
#
# Observed on uv 0.5.26: a single `make -C fl-services/nvflare provision` rewrote
# 1045 lines of fl-api-base/uv.lock, silently bumping pinned transitive versions
# and defeating the 72-hour supply-chain cooldown the lockfile exists to enforce.
#
# This cannot be enforced with `[tool.uv] required-version`: a broken uv fails to
# parse the table that would carry it, so the guard would sit inside the thing it
# guards. Verified — uv 0.5.26 and 0.9.9 both ignore a `required-version` set
# alongside `exclude-newer = "3 days"`. Hence this external check.
#
# Minimum established by bisection against the real value: 0.9.x (0.9.0/.2/.5/.7/.9)
# all reject "3 days"; 0.10.0 is the first release that accepts it.
set -euo pipefail

MIN_UV_VERSION="0.10.0"

if ! command -v uv >/dev/null 2>&1; then
  echo "❌ uv is not installed. FLIP needs uv >= ${MIN_UV_VERSION}." >&2
  echo "   Install it: https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi

current="$(uv --version 2>/dev/null | awk '{print $2}')"
if [[ -z "${current}" ]]; then
  echo "❌ Could not determine the uv version from 'uv --version'." >&2
  exit 1
fi

# Sort the two versions and check the minimum really is the lower one.
lowest="$(printf '%s\n%s\n' "${MIN_UV_VERSION}" "${current}" | sort -t. -k1,1n -k2,2n -k3,3n | head -1)"
if [[ "${lowest}" != "${MIN_UV_VERSION}" ]]; then
  cat >&2 <<EOF
❌ uv ${current} is too old — FLIP needs >= ${MIN_UV_VERSION}.

   Every pyproject.toml sets [tool.uv] exclude-newer = "3 days" for the 72-hour
   supply-chain cooldown. uv only understands that relative form from 0.10.0.
   Older uv WARNS, ignores the setting, and re-resolves uv.lock with no cooldown
   — so this fails loudly here rather than silently rewriting your lockfiles.

   Upgrade:  uv self update    (or: brew upgrade uv)
EOF
  exit 1
fi

echo "✅ uv ${current} (>= ${MIN_UV_VERSION})"
