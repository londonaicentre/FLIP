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

# Release notes for a flip-utils GitHub release, on stdout: only the pull requests that changed
# flip-utils/ since the previous flip-utils release. The platform's notes (highlights, site
# upgrade, release checks) belong to the v<X.Y.Z> release and are linked, not repeated.
#
# Usage: flip-utils-release-notes.sh <version> <tag> <prev-tag or ""> <target-sha>
# Needs GH_TOKEN and GITHUB_REPOSITORY, and a checkout with full history.
#
# Runs after the PyPI publish, which cannot be undone, so nothing here may fail the release:
# without the GitHub API the list degrades to bare PR numbers.

set -uo pipefail

VERSION="$1"
TAG="$2"
PREV_TAG="$3"
TARGET_SHA="$4"
REPO_URL="https://github.com/${GITHUB_REPOSITORY}"
RANGE="${PREV_TAG:+${PREV_TAG}..}${TARGET_SHA}"

# A PR counts when its merge changed flip-utils/ against the branch it merged into, or, for a
# squash merge, when its one commit did. Path-limited `git log` cannot answer the first: it
# drops a merge that matches either parent.
prs=$(
  {
    git log --merges --format='%H %s' "$RANGE" | while read -r sha subject; do
      [[ "$subject" =~ ^Merge\ pull\ request\ \#([0-9]+) ]] || continue
      git diff --quiet "${sha}^1" "$sha" -- flip-utils/ || echo "${BASH_REMATCH[1]}"
    done
    git log --no-merges --format='%s' "$RANGE" -- flip-utils/ | sed -nE 's/.*\(#([0-9]+)\)$/\1/p'
  } | sort -un
)

# GitHub's generated notes give each PR's title and author, and already leave out the PRs
# .github/release.yml excludes (the release plumbing), so keep its lines for our PRs.
API_ARGS=(-X POST -f "tag_name=${TAG}" -f "target_commitish=${TARGET_SHA}")
[[ -n "$PREV_TAG" ]] && API_ARGS+=(-f "previous_tag_name=${PREV_TAG}")
generated=$(gh api "repos/${GITHUB_REPOSITORY}/releases/generate-notes" "${API_ARGS[@]}" --jq '.body' 2>/dev/null || true)

changes=""
if [[ -n "$prs" && -n "$generated" ]]; then
  changes=$(grep -E "^\* .*/pull/($(paste -sd'|' - <<<"$prs"))\$" <<<"$generated" || true)
elif [[ -n "$prs" ]]; then
  changes=$(sed 's/^/* #/' <<<"$prs")
fi

cat <<EOF
# :package: flip-utils ${VERSION}

The \`flip-utils\` Python package (imported as \`flip\`), [on PyPI](https://pypi.org/project/flip-utils/${VERSION}/):

\`\`\`bash
pip install flip-utils==${VERSION}
\`\`\`

These notes cover the package only. For the platform (highlights, breaking changes and what a site
does to upgrade) see the \`v<X.Y.Z>\` releases on the [releases page](${REPO_URL}/releases).

## Changes to the package since ${PREV_TAG:-the start}

${changes:-No pull request changed \`flip-utils/\`; the version moved with the platform release.}
EOF
