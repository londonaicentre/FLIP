#!/usr/bin/env bash
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
# ---------------------------------------------------------------------------
# Migrate flip-fl-base-flower into the FLIP monorepo *with git history preserved*.
#
# Sibling of scripts/migrate-flip-fl-base-history.sh (the NVFLARE migration). Same
# shape — fresh-clone -> git filter-repo keep+relocate -> graft via merge
# --allow-unrelated-histories — but for the Flower repo, which is simpler:
#   * No Git LFS (no .gitattributes) and no tracked binaries, so there is NO
#     drop/inline binary decision to make.
#   * certs/, certs-prod/, data/, supernode-authentication/ are .gitignore'd in
#     the source repo (untracked) — they cannot be history-migrated and are a
#     Phase-1 provisioning concern, so they never enter the rewrite.
#   * docs/ is deliberately NOT migrated: it carries soon-deprecated guidance
#     (e.g. the fliputils_dist additional_contexts dev pattern, obsolete now that
#     flip-utils lives in this repo). Relevant content is hand-folded into
#     README/CONTRIBUTING in a later phase.
# Only the high-provenance SOURCE trees are kept and relocated to the backend-first
# …/flower/ layout established for NVFLARE in #627.
#
# WHY PRESERVE HISTORY: the Flower services/templates/tutorials are thousands of
# lines authored by many people over time. `git blame` / `git log --follow` /
# `git bisect` provenance is a working tool; a flat copy-as-files import collapses
# it to one commit by one author. See the NVFLARE sibling script for full rationale.
#
# WHAT THIS SCRIPT DOES
#   1. Fresh-clones flip-fl-base-flower ($SRC_BRANCH, full history).
#   2. git filter-repo: KEEP only the 3 source trees, RENAME them to …/flower/.
#   3. Verifies (landed file-set, sample blame shows ORIGINAL authors).
#   4. Prints the graft commands; with --apply, grafts into the CURRENT FLIP branch
#      (must be $DEST_BRANCH and clean) via merge --allow-unrelated-histories.
#
# REQUIREMENTS: git >= 2.24, git-filter-repo (pip install git-filter-repo).
#
# USAGE
#   scripts/migrate-flip-fl-base-flower-history.sh          # dry run
#   scripts/migrate-flip-fl-base-flower-history.sh --apply  # also graft into this branch
# ---------------------------------------------------------------------------
set -euo pipefail

SRC_URL="${SRC_URL:-https://github.com/londonaicentre/flip-fl-base-flower.git}"
SRC_BRANCH="${SRC_BRANCH:-develop}"
DEST_REPO="${DEST_REPO:-$(git -C "$(dirname "$0")" rev-parse --show-toplevel)}"
DEST_BRANCH="${DEST_BRANCH:-fl-622-flower-migration}"
WORKDIR="${WORKDIR:-$(mktemp -d)/flip-fl-base-flower-rewrite}"
APPLY=0
[[ "${1:-}" == "--apply" ]] && APPLY=1

# --- Path mapping: source path -> destination (backend-first …/flower/) ------
# KEEP is a WHITELIST matched against ORIGINAL paths; anything not listed is
# dropped. Everything excluded is deliberate: docs/ carries soon-deprecated
# guidance (folded into README/CONTRIBUTING later); top-level Makefile/pyproject/
# uv.lock/README/CLAUDE/CONTRIBUTING/NOTICE/LICENSE collide with FLIP's own;
# .github/ workflows are re-created backend-scoped in Phase 2; check_required_files.sh
# is already cross-backend at fl-apps/ root; certs/data/supernode-auth are untracked.
KEEP=(
  --path fl_services/      # fl-api-flower, fl-base, superlink, supernode, register-supernode-keys.sh
  --path src/              # Flower app templates: standard, evaluation
  --path tutorials/        # xray_classification, 3d_spleen_segmentation(+_evaluation), numpy, standard, evaluation
)
RENAME=(
  --path-rename fl_services/:fl-services/flower/
  --path-rename src/:fl-apps/flower/
  --path-rename tutorials/:fl-tutorials/flower/
)

say() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
die() { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

command -v git >/dev/null             || die "git not found"
command -v git-filter-repo >/dev/null || git filter-repo --version >/dev/null 2>&1 || \
  die "git-filter-repo not found — pip install git-filter-repo"

# --- 1. Fresh clone (full history; never contact an LFS server) -------------
say "Cloning $SRC_URL ($SRC_BRANCH) into $WORKDIR"
mkdir -p "$(dirname "$WORKDIR")"
GIT_LFS_SKIP_SMUDGE=1 git -c filter.lfs.smudge= -c filter.lfs.process= \
  -c filter.lfs.required=false clone --single-branch --branch "$SRC_BRANCH" \
  "$SRC_URL" "$WORKDIR"
cd "$WORKDIR"

# --- 2. Rewrite: keep + relocate, preserving history -----------------------
say "Rewriting history (keep 3 source trees, relocate to …/flower/)"
git filter-repo --force "${KEEP[@]}" "${RENAME[@]}"

# --- 3. Verify -------------------------------------------------------------
say "Landed top-level tree (expect exactly: fl-apps/flower fl-services/flower fl-tutorials/flower)"
git -c core.pager=cat ls-files | awk -F/ '{print $1"/"$2}' | sort -u

SAMPLE="fl-services/flower/fl-api-flower/fl_api/app.py"
if git cat-file -e "HEAD:$SAMPLE" 2>/dev/null; then
  say "Sample blame — $SAMPLE (must show ORIGINAL authors/dates, many commits):"
  # `|| true` swallows the SIGPIPE (141) that `head` raises by closing the pipe
  # early — harmless here, but fatal under `set -o pipefail` (it would abort the
  # script mid-verify, before the --apply graft ever runs).
  git -c core.pager=cat log --follow --format='  %h  %an  %ad  %s' --date=short -- "$SAMPLE" | head -10 || true
  echo "  ..."
  git -c core.pager=cat shortlog -sne --all | head -10 || true
else
  die "expected sample file missing after rewrite: $SAMPLE (KEEP/RENAME table drifted?)"
fi

# --- 4. Graft into FLIP (guarded) ------------------------------------------
GRAFT=$(cat <<EOF
cd "$DEST_REPO"
git remote add flwr-rewrite "$WORKDIR" 2>/dev/null || git remote set-url flwr-rewrite "$WORKDIR"
git fetch flwr-rewrite "$SRC_BRANCH"
git merge --allow-unrelated-histories --no-edit -m "feat(fl): graft flip-fl-base-flower history into fl-services/flower, fl-apps/flower, fl-tutorials/flower (#622)" flwr-rewrite/"$SRC_BRANCH"
git remote remove flwr-rewrite
EOF
)

if [[ "$APPLY" -eq 0 ]]; then
  say "DRY RUN complete. To graft, re-run with --apply, or run these in $DEST_REPO:"
  echo "$GRAFT"
  exit 0
fi

say "Applying graft into $DEST_REPO (branch must be $DEST_BRANCH and clean)"
cur=$(git -C "$DEST_REPO" rev-parse --abbrev-ref HEAD)
[[ "$cur" == "$DEST_BRANCH" ]] || die "FLIP checkout is on '$cur', expected '$DEST_BRANCH' — refusing to graft"
[[ -z "$(git -C "$DEST_REPO" status --porcelain)" ]] || die "FLIP working tree not clean — refusing to graft"
( eval "$GRAFT" )
say "Graft complete. Verify with: git -C $DEST_REPO log --follow -- $SAMPLE"
