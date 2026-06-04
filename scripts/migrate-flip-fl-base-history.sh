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
# Migrate flip-fl-base into the FLIP monorepo *with git history preserved*.
#
# WHY PRESERVE HISTORY FOR THE NON-LFS FILES
# ------------------------------------------
# The code being migrated (the `flip` package, FL app templates, tutorials,
# fl_services, provisioning scripts) is thousands of lines authored by many
# people over a long period. That per-line, per-commit provenance is a working
# tool, not a nicety:
#   - `git blame` answers "who wrote this line, when, and in which commit" — the
#     fastest route to the rationale behind a piece of code and to the person
#     who can explain it.
#   - `git log --follow` / `git bisect` let you trace a regression to the exact
#     change that introduced it, even across this move into the monorepo.
# The "copy as new files" approach (PR #561) collapses all of that into a single
# import commit: every `git blame` then points at the migration, by one author,
# on one date. The rationale trail is gone and `git bisect` can't cross the
# boundary. For source code that is a real, recurring cost.
#
# The Git LFS objects are the ONLY thing that ever blocked a history-preserving
# push, and they are exactly the files whose history has no such value: 5 binary
# PNG diagrams under assets/. Nobody runs `git blame` on a PNG. So we treat the
# two concerns separately — preserve full history for the source files, and for
# the binaries either DROP them from the rewrite (default; re-added later as
# ordinary blobs, which is how FLIP already stores its other diagrams) or INLINE
# them (opt-in, needs git-lfs). Either way the LFS server is never consulted at
# push time, so the rejection that motivated PR #561 cannot happen.
#
# WHAT THIS SCRIPT DOES
# ---------------------
#   1. Fresh-clones flip-fl-base (full history of $SRC_BRANCH).
#   2. Handles the LFS PNGs per $LFS_STRATEGY (drop | inline).
#   3. Uses `git filter-repo` to KEEP only the migrated paths and relocate them
#      to their final FLIP layout — files appear to have *always* lived at their
#      new paths, so blame is pristine with no rename-detection guesswork.
#   4. Verifies (sample blame, and an optional diff against the already-reviewed
#      PR #561 branch to confirm the file set/content matches).
#   5. Prints the exact graft commands. It does NOT touch your FLIP checkout
#      unless you pass --apply.
#
# REQUIREMENTS
#   - git >= 2.24, git-filter-repo (https://github.com/newren/git-filter-repo)
#   - git-lfs ONLY if LFS_STRATEGY=inline
#
# USAGE
#   scripts/migrate-flip-fl-base-history.sh            # dry run: rewrite + verify + print graft cmds
#   scripts/migrate-flip-fl-base-history.sh --apply    # also graft into $DEST_REPO on a new branch
#   LFS_STRATEGY=inline scripts/migrate-flip-fl-base-history.sh
#   SRC_BRANCH=main DEST_BRANCH=455-code-migration-history scripts/migrate-flip-fl-base-history.sh --apply
# ---------------------------------------------------------------------------
set -euo pipefail

# --- Config (override via environment) -------------------------------------
SRC_URL="${SRC_URL:-https://github.com/londonaicentre/flip-fl-base.git}"
SRC_BRANCH="${SRC_BRANCH:-develop}"          # flip-fl-base default branch
DEST_REPO="${DEST_REPO:-$(git -C "$(dirname "$0")" rev-parse --show-toplevel)}"
DEST_BASE_BRANCH="${DEST_BASE_BRANCH:-develop}"
DEST_BRANCH="${DEST_BRANCH:-455-code-migration-history}"
WORKDIR="${WORKDIR:-$(mktemp -d)/flip-fl-base-rewrite}"
LFS_STRATEGY="${LFS_STRATEGY:-drop}"         # drop | inline
COMPARE_BRANCH="${COMPARE_BRANCH:-455-code-migration}"  # existing PR #561 branch, for reconciliation
APPLY=0
[[ "${1:-}" == "--apply" ]] && APPLY=1

# --- Path mapping: source path (in flip-fl-base) -> destination (in FLIP) ---
# VERIFY these against PR #561's intended layout before trusting --apply. The
# reconciliation diff in step 4 is the source of truth: tweak this table until
# the diff against the reviewed branch is empty.
#
# KEEP entries are matched against ORIGINAL paths (whitelist; anything not
# listed is dropped). RENAME entries relocate them. fl_services/ and deploy/
# keep their names (identity), so they need a KEEP but no RENAME.
KEEP=(
  --path flip/                              # the flip python package
  --path pyproject.toml                     # top-level package metadata
  --path uv.lock
  --path tests/                             # flip package tests
  --path src/                               # FL templates: standard, fed_opt, evaluation, diffusion_model
  --path tutorials/                         # xray_classification, spleen seg, synthesis, evaluation, testing
  --path scripts/check-dev-paths.sh
  --path scripts/merge-job-dirs.sh
  --path check_required_files.sh
  --path scripts/provision-network.sh       # VERIFY: PR routes provisioning under deploy/
  --path scripts/provision-additional-client.sh
  --path net-1_project.yml                  # NVFLARE provisioning project files (PR #561 routes under deploy/providers/)
  --path net-1_project_prod.yml
  --path net-1_project_stag.yml
  --path net-2_project.yml
  --path README.md                           # flip-utils package README
  --path docs/conf.py
  --path docs/index.rst
  --path docs/overview.rst
  --path docs/reference/
  --path docs/requirements.txt
  --path docs/_static/
  --path docs/_templates/
  --path fl_services/                       # identity
  --path deploy/                            # identity (merges with FLIP's deploy/)
)
RENAME=(
  --path-rename flip/:flip-utils/flip/
  --path-rename pyproject.toml:flip-utils/pyproject.toml
  --path-rename uv.lock:flip-utils/uv.lock
  --path-rename tests/:flip-utils/tests/
  --path-rename src/:fl-apps/
  --path-rename tutorials/:fl-apps/tutorials/
  --path-rename scripts/check-dev-paths.sh:fl-apps/scripts/check-dev-paths.sh
  --path-rename scripts/merge-job-dirs.sh:fl-apps/scripts/merge-job-dirs.sh
  --path-rename check_required_files.sh:fl-apps/scripts/check_required_files.sh
  --path-rename scripts/provision-network.sh:deploy/scripts/provision-network.sh
  --path-rename scripts/provision-additional-client.sh:deploy/scripts/provision-additional-client.sh
  --path-rename net-1_project.yml:deploy/providers/net-1_project.yml
  --path-rename net-1_project_prod.yml:deploy/providers/net-1_project_prod.yml
  --path-rename net-1_project_stag.yml:deploy/providers/net-1_project_stag.yml
  --path-rename net-2_project.yml:deploy/providers/net-2_project.yml
  --path-rename deploy/release.sh:deploy/scripts/release.sh
  --path-rename README.md:flip-utils/README.md
  --path-rename docs/:flip-utils/docs/
)
# Opt-in: carry the LFS PNGs across as ordinary blobs (needs git-lfs).
if [[ "$LFS_STRATEGY" == "inline" ]]; then
  KEEP+=( --path assets/ )
  RENAME+=( --path-rename assets/:docs/source/assets/flip-fl-base/ )
fi

say() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
die() { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# --- Prerequisites ---------------------------------------------------------
command -v git >/dev/null            || die "git not found"
command -v git-filter-repo >/dev/null || die "git-filter-repo not found — install it (pip install git-filter-repo)"
if [[ "$LFS_STRATEGY" == "inline" ]]; then
  command -v git-lfs >/dev/null || die "LFS_STRATEGY=inline needs git-lfs installed"
fi
[[ "$LFS_STRATEGY" =~ ^(drop|inline)$ ]] || die "LFS_STRATEGY must be 'drop' or 'inline'"

# --- 1. Fresh clone of the source branch (full history) --------------------
say "Cloning $SRC_URL ($SRC_BRANCH) into $WORKDIR"
mkdir -p "$(dirname "$WORKDIR")"
if [[ "$LFS_STRATEGY" == "drop" ]]; then
  # Never contact the LFS server; the pointer files are dropped anyway.
  GIT_LFS_SKIP_SMUDGE=1 git -c filter.lfs.smudge= -c filter.lfs.process= \
    -c filter.lfs.required=false clone --single-branch --branch "$SRC_BRANCH" \
    "$SRC_URL" "$WORKDIR"
else
  git clone --single-branch --branch "$SRC_BRANCH" "$SRC_URL" "$WORKDIR"
fi
cd "$WORKDIR"

# --- 2. LFS handling -------------------------------------------------------
if [[ "$LFS_STRATEGY" == "inline" ]]; then
  say "Inlining LFS objects (fetch all, then export pointers -> real blobs)"
  git lfs fetch --all
  git lfs migrate export --everything --include="*.png"
else
  say "LFS strategy: drop — PNG diagrams are excluded from the rewrite (re-add as normal files later)"
fi

# --- 3. Rewrite: keep + relocate, preserving history -----------------------
say "Rewriting history (keep migrated paths, relocate to FLIP layout)"
git filter-repo --force "${KEEP[@]}" "${RENAME[@]}"

# --- 4. Verify -------------------------------------------------------------
say "Result tree (top level)"
git -c core.pager=cat ls-files | awk -F/ '{print $1"/"$2}' | sort -u | sed 's/\/$//'

SAMPLE="flip-utils/flip/core/base.py"
if git cat-file -e "HEAD:$SAMPLE" 2>/dev/null; then
  say "Sample blame — $SAMPLE (should show ORIGINAL authors/dates, many commits):"
  git -c core.pager=cat log --follow --format='  %h  %an  %ad  %s' --date=short -- "$SAMPLE" | head -10
  echo "  ..."
  git -c core.pager=cat shortlog -sne --all | head -10
fi

say "Reconciliation against the reviewed PR #561 branch ($COMPARE_BRANCH)"
echo "After grafting, run this in $DEST_REPO to confirm the file set/content matches"
echo "what was already reviewed (empty output for a path == identical content):"
echo "    git fetch origin $COMPARE_BRANCH"
echo "    git diff --stat origin/$COMPARE_BRANCH -- flip-utils fl-apps fl_services"
echo "Adjust the KEEP/RENAME table above until that diff is empty, then re-run."

# --- 5. Graft into FLIP (guarded) ------------------------------------------
GRAFT_CMDS=$(cat <<EOF
cd "$DEST_REPO"
git fetch origin "$DEST_BASE_BRANCH"
git switch -c "$DEST_BRANCH" "origin/$DEST_BASE_BRANCH"
git remote add flbase-rewrite "$WORKDIR" 2>/dev/null || git remote set-url flbase-rewrite "$WORKDIR"
git fetch flbase-rewrite "$SRC_BRANCH"
git merge --allow-unrelated-histories --no-edit "flbase-rewrite/$SRC_BRANCH"
git remote remove flbase-rewrite
# Resolve any overlaps under deploy/ by keeping BOTH sides' files (do not discard).
EOF
)

if [[ "$APPLY" -eq 1 ]]; then
  say "Grafting into $DEST_REPO on branch $DEST_BRANCH"
  ( eval "$GRAFT_CMDS" )
  say "Done. Verify blame in FLIP, run the reconciliation diff above, then push:"
  echo "    git push -u origin $DEST_BRANCH"
else
  say "DRY RUN — FLIP checkout untouched. Rewritten repo is at:"
  echo "    $WORKDIR"
  echo
  echo "To graft it into FLIP (history preserved), run:"
  echo "$GRAFT_CMDS"
  echo
  echo "Re-run with --apply to perform the graft automatically."
fi
