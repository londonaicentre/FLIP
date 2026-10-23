---
name: release
description: Prepare and cut a FLIP platform release — version bumps, release notes, the pre-release gates on a GPU host, the release PRs, and the post-merge follow-through.
---

<!--
Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at
    http://www.apache.org/licenses/LICENSE-2.0
Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# FLIP release

[`CONTRIBUTING.md` § Cutting a release](../../../CONTRIBUTING.md#cutting-a-release) is the procedure: versioning
rules, the two tag namespaces, the pre-release checklist, merge rules and roll-out. Read it and
`.github/workflows/release.yml` first; this skill adds only what an agent needs on top and does not restate them.

## Scope and autonomy

Preparation runs unattended, often overnight: discovery, fixes, gates, checklist evidence, PRs into `develop`. Don't
ask routine questions; pick the safe default and record it in the report. Leftovers from an earlier attempt at this
same release (its worktree, isolated stack, orphaned simulator processes, unverified ticks) are yours to replace.
Stop only on a blocker that needs the user, for example a live process owned by someone else, and report it with
what is already done. **Without an explicit request for that specific
action, never** open the `develop` → `main` PR, merge anything, push or create a tag, dispatch a deploy or apply,
attest `fl_quiesced`, or upgrade a site.

## The pipeline

Every mechanical step is a make target over `scripts/release/` (summarised above `release-prepare` in
the root `Makefile`). Your work is the judgement around them: scope, the notes' prose, failures, the handoff.

1. **State.** Fetch; note `develop`/`main` heads and the latest `v*.*.*` and `flip-utils-v*.*.*` tags. Work in a new
   worktree off `origin/develop` (branch `release-v<X.Y.Z>`); leave the main checkout's untracked files alone.
   `develop` CI must be green and every intended PR merged.
2. **`make release-prepare TAG=v<X.Y.Z>`** (`DRY_RUN=1` first). It refuses while a merged PR is unlabelled — label
   it from `.github/release.yml`'s categories and rerun. It prints the open Dependabot alerts: fix every actionable one
   **together, in one** `v<X.Y.Z> dependency fixes` PR (never one PR per package, and never in the bump PR), merged
   before the gates run so they test it. A fix still inside the 72 h install cooldown is not installable: add it to
   that PR once the cooldown expires, or ship without it and name it in the report. It bumps the versions, runs
   `make lock` and empties *Release checks*.
3. **Notes and the draft PR.** Curate `.github/RELEASE_NOTES_TEMPLATE.md`, especially *Site upgrade* (required or
   not, hub/site order or one Deployment-Mode window, refreshed kit or not), from the merged PRs. Commit (signed
   off), push, and open the draft `Release v<X.Y.Z>` → `develop` with the `release` label, which keeps release
   plumbing out of the generated notes. The gates run at its head; if the head moves,
   rerun the gates it affects.
4. **On the GPU host, at the PR head:** `make release-gates TAG=… PR=<n>`. It refuses a dirty checkout or one that
   is not the PR head. It runs the quickest gates first (unit, integration, then both tutorial suites) on a quiet
   host, then brings up the isolated instance (`release-instance`), verifies provenance before each smoke, records
   `release-gates/<TAG>/<gate>.json`, keeps every container's log when a stack gate fails, and tears the instance
   down.
5. **`make release-record TAG=… PR=<n> APPLY=1`** writes *Release checks* from those results (it refuses results
   from another commit). Commit and push; wait for CI to go green; mark the PR ready; request review from a
   maintainer other than the person running the release (e.g. `garciadias`, or `atriaybagur` when garciadias runs
   it) and confirm with `gh pr view <n> --json reviewRequests`. A gate that did not pass stays `[ ]` and goes in the
   report as pending or failed — never as passed.

## The GPU host

The gates run on a Linux host with an NVIDIA GPU and Docker, never on a laptop. For example, the core team uses the
shared dev desktop `bioeng625-pc` over SSH. If the user hasn't named the host, take it from your notes or memory, and
ask only if you find none. Keep a release worktree there at the PR head and run the targets in the background. It
has to be a dedicated one: `release-gates` refuses a checkout whose `trust/.env.<KIT>.development` kits another
stack wrote.

- **Inputs.** The tutorials need the datasets, the prostate prep and the Ark+ checkpoint (CONTRIBUTING).
  `fl-tutorials/data/` is gitignored, so point a new worktree's at the main checkout's copy (symlink) rather than
  downloading again.
- **Shared host.** The instance (`release-instance`, run by `release-gates`) gets its own `FLIP_INSTANCE`, ports (`PORT_OFFSET`, default
  10000), secrets and image tag (`rel<XYZ>`); it builds every repo image from the checkout and never touches the
  shared `:dev`/`:stag` tags or the default `deploy` stack. Pick another `PORT_OFFSET` if those ports are taken.
  `FORCE=1` replaces the worktree's own env and kit files only. A Flower simulator run hijacked by an orphaned
  SuperLink is someone's leftover process: check whose before killing it.
- **Partial reruns.** `GATES=smoke-nvflare,smoke-flower KEEP=1` reruns a subset and leaves the instance up;
  `CONTINUE=1` runs past a failure. Results for a gate not reached are written as `not-run`, so a stale pass never
  stands in for one.

## After the bump PR merges (only on request)

Opening and merging the `develop` → `main` PR each need the user's explicit request. `make release-main-check
TAG=v<X.Y.Z> [OWED=<file>]` is read-only and does the rest. With no PR yet, it prints the `gh pr create` command,
with a body carrying the after-merge checklist and the owed items (one per line in `OWED`; take them from your
notes). Run that command only when asked. With the PR open, it fails unless the PR is MERGEABLE,
`validate_branch_origin` passed, `check-version-bump` passed (when flip-utils changed), and the release-notes
preview comment is present. Read that comment. When asked to merge, merge it **with a merge commit**.

The merge triggers two prod Terraform applies: the push apply at `sha-`, then the `release.yml` apply at
`v<X.Y.Z>` once the twelve image builds are green. Both can hold on the FL gate. Only the user may attest
`fl_quiesced`. Nothing may merge to `main` until the release apply has run.

Then check that the Release page, GHCR `:v<X.Y.Z>` (all twelve) and PyPI (if bumped) are correct, and that prod
`/api/health` reports `v<X.Y.Z>`. flip-ui is **not** deployed by the merge (#1186 is open):
`make -C deploy/providers/AWS deploy-ui HUB=<hub>` from a checkout at the tag. A fresh worktree has no Terraform
state, so run `make -C deploy/providers/AWS init HUB=<hub>` there first. Sites upgrade themselves: point
operators to *Site upgrade* and `docs/source/sys-admin/admin-upgrading-sites.rst`.
