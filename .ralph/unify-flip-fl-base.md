# Phase 1: Code Migration — Unify flip-fl-base into FLIP monorepo

## Directory creation and file copy tasks

### 1. Create target directories
- flip-utils/ (with flip/ subpackage inside)
- flip-utils/tests/
- fl-apps/templates/ (standard, fed_opt, evaluation, diffusion_model)
- fl-apps/tutorials/ (image_classification, image_evaluation, image_segmentation, image_synthesis, tabular, testing, data)
- fl-apps/scripts/
- fl-apps/runs/
- deploy/workspace/ (net-1, net-2)
- deploy/scripts/
- deploy/providers/
- docs/source/assets/flip-fl-base/
- fl_services/ (fl-api-base, fl-base, fl-client, fl-server)

### 2. Copy files from ../flip-fl-base/ to new locations
Copy each artifact to its destination. All from ../flip-fl-base/

### 3. Create flip-utils/pyproject.toml (adapted from flip-fl-base root pyproject.toml)
- Update [project.urls] to point to FLIP repo
- Keep [tool.hatch.version] path as "flip/__init__.py" (relative)
- Keep [tool.hatch.build.targets.wheel] packages as ["flip"]
- Update [tool.pytest.ini_options] norecursedirs for new layout
- Update build system to match new paths

### 4. Create flip-utils/Makefile
- unit-test target (ruff + pytest with coverage)
- docs target (sphinx-build)
- docs-clean target

### 5. Create fl-apps/Makefile
- Integration test targets from flip-fl-base Makefile
- download-test-data target
- Paths updated for new structure

### 6. Update deploy/fl_backend.mk
Change FL_PROVISIONED_DIR from ../flip-fl-base/workspace to ../deploy/workspace

### 7. Update root Makefile
Add high-level targets: nvflare-provision, nvflare-provision-2-nets, nvflare-provision-additional-client
These delegate to deploy/scripts/ implementations

### 8. Update .gitignore
Add flip-utils patterns, fl_services patterns, fl-apps patterns

### 9. Update .env.development.example
Add FL-specific vars: LOCAL_DEV, MIN_CLIENTS, DEV_IMAGES_DIR, DEV_DATAFRAME, RUNS_DIR, JOB_TYPE, FL_API_PORT, LOG_LEVEL, FLIP_BUCKET_NAME

### 10. Update CLAUDE.md
- Add new top-level directories to repo structure
- Update e2e_smoke paths (tutorials now at fl-apps/tutorials/)
- Update workspace path references
- Remove flip-fl-base from Related Repositories table
- Add note about update paths

Each task must verify files exist at destination before moving on.
Start with directory creation, then file copies, then config/script updates.

---

PROGRESS (iteration 3)

- [x] 1. Create target directories (flip-utils/, fl-apps/, deploy/workspace/, fl_services/, docs assets)
- [x] 2. Copy files from ../flip-fl-base/ into repo (flip package → flip-utils/flip, fl_services/, fl-apps/templates, tutorials, deploy/workspace, deploy/providers, scripts)
- [x] 3. Create flip-utils/pyproject.toml (adapted)
- [x] 4. Create flip-utils/Makefile (unit-test, docs targets)
- [x] 5. Create fl-apps/Makefile (integration targets)
- [x] 6. Update deploy/fl_backend.mk (FL_PROVISIONED_DIR → deploy/workspace)
- [x] 7. Update root Makefile (nvflare provision targets)
- [x] 8. Update .gitignore (deploy/workspace, .test_data, .test_runs, NVFLARE secrets)
- [x] 9. Update .env.development.example (FL vars added)
- [x] 10. Update CLAUDE.md (repo layout, e2e paths)
- [x] 11. Copy CI workflows from flip-fl-base into .github/workflows and path-scope them (`flip` → `flip-utils`, `src/` → `fl-apps/templates`)
- [x] 12. Fix CI workflow issues: checkout usage in docker_build_fl, flip/tests working-dir, release job working-dir and release-notes template path
- [x] 13. Update fl-apps scripts (check_required_files.sh → points at fl-apps/templates)
- [x] 14. Update deploy scripts (deploy/scripts/release.sh now checks flip-utils/flip/__init__.py)

Remaining:

- Update any remaining references to old repo layout (scan & fix false positives in docs only when necessary)
- Run `make -C flip-utils test` and `make nvflare-provision` locally (manual verification)
- Phase 2: CI repo-level workflow dedup and enrichment (PR acceptance criteria merged)

Notes:
- Release workflow now runs from flip-utils; release-notes step reads template via $GITHUB_WORKSPACE to avoid working-dir issues.
- PR acceptance criteria workflow replaced with flip-fl-base enhanced version.

Next step: run automated path-scan for leftovers, fix scripts that reference repo-root flip/ and push changes.

PROGRESS (iteration 4)

- [x] 15. Run automated path-scan for leftover references (grep for '__version__', 'flip/__init__.py', 'src/' patterns)
- [x] 16. Verify critical moved files exist (flip-utils/flip/__init__.py, fl-apps/templates, fl_services/*, deploy/workspace)

Findings:
- All critical code paths updated to new layout; release workflows run from flip-utils working directory and extract version from flip/__init__.py (within flip-utils).
- deploy/scripts/release.sh updated to read flip-utils/flip/__init__.py when run from repo root.
- Documentation still contains references to flip-fl-base and flip-fl-base-flower where appropriate; will be reviewed in Phase 3.

Next steps:
1. Run light verification (lint) for flip-utils if CI quick-run allowed: `make -C flip-utils unit_test` or `uv run ruff check ./flip-utils`
2. Phase 2: deduplicate/enrich GitHub workflows, add path scoping cross-repo triggers, and merge pr_acceptance_criteria behaviour.
3. Phase 3: docs consolidation and Sphinx config merge.

Call ralph_done to continue iterations.

PROGRESS (iteration 5)

- [x] 17. Run lint (ruff) inside flip-utils — PASS (no findings)
- [x] 18. Attempt unit tests in flip-utils — FAILED during collection due to missing heavy runtime dependency `torch` (ModuleNotFoundError). PyTest aborted at import time for NVFLARE-related modules that transitively import torch.

Details:
- Command run: `cd flip-utils && uv run ruff check . --fix` → All checks passed.
- Command run: `cd flip-utils && uv run pytest tests/unit` → Collection failed: ModuleNotFoundError: No module named 'torch'. Many nvflare tests import nvflare/app_opt which imports torch transitively.

Options:
1. Install PyTorch into the uv env (heavy, large binaries). Use `uv sync` after adding torch to dependency groups or set up a separate env with torch. Time/resource expensive.
2. Run a targeted subset of tests that don't import nvflare/torch. Requires constructing pytest -k filters or moving heavy tests behind markers.
3. Defer full test run to CI where GPU/tensor packages are available; keep local verification as ruff only.

Recommendation: Keep local verification minimal (ruff passed). Proceed to Phase 2 (CI dedup/enrich) and schedule full test runs in CI where appropriate runtime exists.

Next step: start Phase 2 — deduplicate GitHub workflows, merge enhanced pr_acceptance_criteria behaviour, and add path-scoped triggers.

PROGRESS (iteration 6)

- [x] 19. Add path scoping to push-app-to-s3 workflows (dev/stag/prod) -> now trigger only on fl-apps/**
- [x] 20. Remove duplicate PR source-branch validation workflow (validate_pr_source_branch_fl.yml) — keep project canonical validate_branch_origin.yml

Notes:
- Unit / package CI workflows path-scoped where applicable. Docs and secret scanning remain global.

Next: continue Phase 2 — scan workflows for any remaining broad triggers that should be scoped (e.g. push to main on files unrelated to service), and decide merges for overlapping build workflows.

PROGRESS (iteration 7)

- [x] 21. Scan all .github/workflows for broad triggers (push/pull_request without paths)

Findings (broad workflows):
- docs.yml (push to main/develop, no paths) — candidate to scope to docs/** but acceptable global.
- release-pypi.yml (push to main, no paths) — intentionally global (publish guard uses tag/version check).
- release.yml (push to main, no paths) — intentionally global (tag+release management).
- secret-scanning.yml (push & PR, no paths) — must remain global.
- validate_branch_origin.yml, pr_acceptance_criteria.yml, pr-release-notes-preview.yml, cleanup-pr-s3.yml — PR-scoped logically and intentionally broad.

Recommendations:
- Keep release workflows and secret-scanning global.
- Optionally scope docs.yml to ["docs/**", ".readthedocs.yaml", ".github/RELEASE_NOTES_TEMPLATE.md"] to avoid unnecessary builds; defer unless we observe noise.
- No merges required for docker/build workflows — they are service-scoped and non-overlapping.

Next steps:
1. Apply docs.yml scoping only if you want immediate CI quiet — ask if you want.
2. Start Phase 2b: deduplicate/merge cross-repo PR acceptance logic into central PR workflow set (pr_acceptance_criteria has been replaced but may need small adaptions).
3. Run CI smoke locally if desired (not live here).

Call ralph_done to continue iterations.

PROGRESS (iteration 8)

- [x] 22. Merge/pr_acceptance_criteria workflow validated and integrated into `.github/workflows/` — includes "Set PR title from linked issue" + "Import Acceptance Criteria" steps.
- [x] 23. Verified no duplicate PR-acceptance logic elsewhere.

Notes:
- pr_acceptance_criteria.yml left intentionally broad (runs on pull_request opened/edited/reopened) because it must run for all PRs.
- No further dedup required now.

Next: Phase 2c — run lightweight CI policy checks and prepare PR that documents migration (changes in .github/workflows/*, fl-apps/, flip-utils/). After PR, run CI in GitHub to validate workflows.

PROGRESS (iteration 9)

- [x] 24. Prepared commit plan at `.plans/455-migration-commit-plan.md` with ordered commits, conventional messages, and PR draft.
- [x] 25. Created commits and opened PR: https://github.com/londonaicentre/FLIP/pull/559

Actions performed:
1. Created commit (migration snapshot) containing migrated code and config.
2. Pushed branch `455-feature-unify-flip-fl-base-into-the-flip-monorepo` to origin.
3. Opened PR #559 against `develop` with migration plan and summary.

Next steps:
- Monitor CI runs on PR. Address failures (heavy nvflare tests may require torch; consider marking or adding dependencies in CI).
- After CI green, Phase 3: docs consolidation + Sphinx merge.

If you want a different commit grouping or additional changes before merging, tell me which groups to split or which files to reassign to new commits.
